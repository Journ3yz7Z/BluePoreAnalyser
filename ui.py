from pathlib import Path
import os,json,queue,threading,datetime,uuid,math
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,font as tkfont
import numpy as np
from PIL import Image,ImageTk
from engine import save_one,display_image
from reporting import write_report,write_batch
from review import Review
from viewport import Viewport

HOME=Path(__file__).resolve().parent
EXTENSIONS={'.jpg','.jpeg','.png','.tif','.tiff','.bmp'}

class WrapBar(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent);self.items=[];self.job=None;self.bind('<Configure>',self.schedule)
    def add(self,w):self.items.append(w);self.schedule()
    def schedule(self,event=None):
        if self.job:self.after_cancel(self.job)
        self.job=self.after_idle(self.layout)
    def layout(self):
        self.job=None;width=max(150,self.winfo_width());used=0;y=0;rowheight=0
        for w in self.items:
            needed=w.winfo_reqwidth()+10
            if used+needed>width and used:y+=rowheight+8;used=0;rowheight=0
            w.place(x=used,y=y+4);used+=needed;rowheight=max(rowheight,w.winfo_reqheight())
        self.configure(height=y+rowheight+8)

class App:
    def __init__(self,root):
        self.root=root;root.title('蓝色孔隙面积分析 · 0.2.1 流畅复核版')
        root.geometry(f'{max(760,min(1380,root.winfo_screenwidth()-70))}x{max(580,min(940,root.winfo_screenheight()-90))}');root.minsize(760,580)
        self.settings={}
        try:self.settings=json.loads((HOME/'settings.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.events=queue.Queue();self.busy=False;self.saving=False;self.viewport=None;self.cancel=threading.Event();self.entries={};self.paths=[];self.failures=[]
        self.batch=None;self.out=None;self.current=None;self.current_id=None;self.photos=[];self.zoom=1.;self.center=(0.,0.);self.render_job=None;self.drag=None
        self.mode=tk.StringVar(value='平移');self.view=tk.StringVar(value='绿色＋橙色');self.radius=tk.IntVar(value=12)
        self.exclude=tk.BooleanVar(value=True);self.fontsize=tk.IntVar(value=int(self.settings.get('font_size',13)))
        self.status=tk.StringVar(value='选择照片开始分析，或打开已有结果进行人工复核。');self.summary=tk.StringVar(value='绿色计入面积；橙色未计入。可补选、移除或确认排除。')
        top=ttk.Frame(root,padding=(12,8));top.pack(fill='x');title=ttk.Frame(top);title.pack(fill='x')
        ttk.Label(title,text='蓝色孔隙面积分析',font=('Microsoft YaHei',20,'bold')).pack(side='left')
        ttk.Label(title,text='文字大小').pack(side='right',padx=6)
        self.fontspin=ttk.Spinbox(title,from_=11,to=20,width=3,textvariable=self.fontsize,command=self.set_font);self.fontspin.pack(side='right')
        for event in ['<Return>','<FocusOut>']:self.fontspin.bind(event,lambda e:self.set_font())
        self.actions=WrapBar(top);self.actions.pack(fill='x');self.buttons={}
        for text,fn in [('选择图片',self.choose),('选择文件夹',self.choose_folder),('开始分析',self.run),('停止队列',self.stop),('继续队列',self.resume),('重试失败',self.retry),('打开已有结果',self.open_existing),('结果报告',self.open_report),('结果文件夹',self.open_folder)]:
            b=ttk.Button(self.actions,text=text,command=fn);self.actions.add(b);self.buttons[text]=b
        self.exclude_check=ttk.Checkbutton(self.actions,text='初始排除右下角标注区',variable=self.exclude);self.actions.add(self.exclude_check)
        self.progress=ttk.Progressbar(top);self.progress.pack(fill='x',pady=4)
        self.status_label=ttk.Label(top,textvariable=self.status,wraplength=950);self.status_label.pack(fill='x')
        self.body=ttk.Panedwindow(root,orient='horizontal');self.body.pack(fill='both',expand=True,padx=12)
        left=ttk.Frame(self.body,width=210);right=ttk.Frame(self.body);self.body.add(left,weight=0);self.body.add(right,weight=1)
        ttk.Label(left,text='图片列表 · 点击切换').pack(anchor='w')
        self.tree=ttk.Treeview(left,columns=('area','status'),show='tree headings',selectmode='browse',height=8)
        for col,label,width in [('#0','图片',90),('area','面积%',65),('status','状态',90)]:
            self.tree.heading(col,text=label);self.tree.column(col,width=width,minwidth=50)
        scroll=ttk.Scrollbar(left,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y')
        self.tree.pack(fill='both',expand=True);self.tree.bind('<<TreeviewSelect>>',self.select_result)
        self.summary_label=ttk.Label(right,textvariable=self.summary,wraplength=950);self.summary_label.pack(fill='x',padx=8,pady=6)
        self.review_bar=bar=WrapBar(right);bar.pack(fill='x',padx=8)
        self.modebox=ttk.Combobox(bar,textvariable=self.mode,values=['平移','加入','橙色转绿色','移除','确认排除','替换排除框'],state='readonly',width=10);bar.add(self.modebox)
        bar.add(ttk.Label(bar,text='笔半径（原图像素）'));bar.add(ttk.Spinbox(bar,from_=1,to=200,width=4,textvariable=self.radius))
        for text,fn in [('撤销',self.undo),('恢复自动',self.reset),('保存复核',self.save_review)]:bar.add(ttk.Button(bar,text=text,command=fn))
        self.viewbar=bar=WrapBar(right);bar.pack(fill='x',padx=8)
        combo=ttk.Combobox(bar,textvariable=self.view,values=['绿色＋橙色','只看橙色','只看计入区域'],state='readonly',width=13);bar.add(combo);combo.bind('<<ComboboxSelected>>',lambda e:self.schedule_render())
        for text,fn in [('适应窗口',self.fit),('原图100%',self.actual),('放大',lambda:self.change_zoom(1.4)),('缩小',lambda:self.change_zoom(1/1.4))]:bar.add(ttk.Button(bar,text=text,command=fn))
        frame=ttk.Frame(right);frame.pack(fill='both',expand=True);frame.columnconfigure(0,weight=1);frame.columnconfigure(1,weight=1);frame.rowconfigure(1,weight=1)
        ttk.Label(frame,text='原图（同步缩放）').grid(row=0,column=0,pady=4);ttk.Label(frame,text='复核图（在此涂画）').grid(row=0,column=1,pady=4)
        self.canvases=[]
        for i in range(2):
            c=tk.Canvas(frame,bg='#25313c',highlightthickness=0,width=200,height=220);c.grid(row=1,column=i,sticky='nsew',padx=4);self.canvases.append(c)
            c.bind('<Configure>',lambda e:self.schedule_render());c.bind('<MouseWheel>',lambda e,c=c:self.wheel(e,c))
            c.bind('<ButtonPress-1>',lambda e,c=c:self.press(e,c));c.bind('<B1-Motion>',lambda e,c=c:self.motion(e,c));c.bind('<ButtonRelease-1>',lambda e,c=c:self.release(e,c))
            c.bind('<ButtonPress-3>',lambda e,c=c:self.press(e,c,True));c.bind('<B3-Motion>',lambda e,c=c:self.motion(e,c));c.bind('<ButtonRelease-3>',lambda e,c=c:self.release(e,c))
        self.hint_label=ttk.Label(root,text='滚轮缩放；右键拖动平移。橙色转绿色：仅加入笔刷内的橙色候选。其他编辑在右图涂画，排除框拖出矩形。保存后更新报告。',wraplength=1100);self.hint_label.pack(fill='x',padx=12,pady=8)
        root.bind('<Configure>',self.resize);root.protocol('WM_DELETE_WINDOW',self.close);root.bind('<Control-z>',lambda e:self.undo());root.bind('<Control-s>',lambda e:self.save_review())
        self.set_font();self.update_buttons();root.after(100,self.poll)

    def set_font(self):
        try:n=max(11,min(20,int(self.fontsize.get())))
        except (ValueError,tk.TclError):n=13
        self.fontsize.set(n)
        for name in ['TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont']:tkfont.nametofont(name).configure(family='Microsoft YaHei',size=n)
        s=ttk.Style();s.configure('Treeview',rowheight=int(n*2.4),font=('Microsoft YaHei',n));s.configure('Treeview.Heading',font=('Microsoft YaHei',n))
        self.settings['font_size']=n;self.persist_settings()
        for bar in [self.actions,self.review_bar,self.viewbar]:bar.schedule()

    def persist_settings(self):
        try:(HOME/'settings.json').write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:self.status.set('偏好设置无法保存；本次操作仍可继续。')

    def resize(self,e):
        if e.widget is self.root:
            self.status_label.configure(wraplength=max(300,e.width-40));self.hint_label.configure(wraplength=max(300,e.width-40));self.summary_label.configure(wraplength=max(250,e.width-350));self.schedule_render()

    def update_buttons(self):
        for text in ['选择图片','选择文件夹','开始分析','重试失败','打开已有结果']:self.buttons[text].configure(state='disabled' if self.busy or self.saving else 'normal')
        self.buttons['停止队列'].configure(state='normal' if self.busy else 'disabled');self.exclude_check.configure(state='disabled' if self.busy else 'normal')
        self.buttons['继续队列'].configure(state='normal' if self.batch and 'pending' in self.batch['states'] and not self.busy and not self.saving else 'disabled')
        for widget in self.review_bar.items:
            if isinstance(widget,ttk.Combobox):widget.configure(state='disabled' if self.saving else 'readonly')
            elif isinstance(widget,(ttk.Button,ttk.Spinbox)):widget.configure(state='disabled' if self.saving else 'normal')

    def guard_dirty(self):
        if self.saving:self.status.set('正在后台保存，请保存完成后再切换图片。');return False
        if self.current and self.current.dirty:
            answer=messagebox.askyesnocancel('尚未保存','当前图片有未保存的人工复核。是否保存？\n“否”仅放弃本次未保存的修改。')
            if answer is None:return False
            if answer:self.save_review();return False
        return True

    def set_paths(self,paths):
        if not self.guard_dirty():return False
        self.paths=list(dict.fromkeys(map(str,paths)));self.entries={};self.current=None;self.viewport=None;self.current_id=None;self.failures=[];self.out=None;self.batch=None
        self.tree.delete(*self.tree.get_children())
        for i,p in enumerate(self.paths):self.tree.insert('','end',iid=str(i),text=Path(p).name,values=('','待分析'))
        self.status.set(f'已选择 {len(self.paths)} 张图片。');self.update_buttons();self.schedule_render();return True

    def choose(self):
        if self.busy:return
        paths=filedialog.askopenfilenames(initialdir=self.settings.get('input_dir',str(HOME.parents[1]/'Data')),filetypes=[('图片','*.jpg *.jpeg *.png *.tif *.tiff *.bmp')])
        if paths:self.set_paths(paths);self.settings['input_dir']=str(Path(paths[0]).parent);self.persist_settings()

    def choose_folder(self):
        if self.busy:return
        p=filedialog.askdirectory(initialdir=self.settings.get('input_dir',str(HOME)),title='选择图片文件夹（包括子文件夹）')
        if p:self.set_paths(sorted(str(f) for f in Path(p).rglob('*') if f.suffix.lower() in EXTENSIONS));self.settings['input_dir']=p;self.persist_settings()

    def run(self):
        if self.busy:return
        if not self.paths:messagebox.showinfo('选择图片','请先选择图片或文件夹。');return
        if not self.guard_dirty():return
        target=filedialog.askdirectory(title='选择结果保存位置',initialdir=self.settings.get('output_dir',str(HOME)))
        if not target:return
        try:
            out=Path(target)/('蓝色孔隙分析_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);out.mkdir()
            batch={'inputs':list(self.paths),'exclude_annotation':bool(self.exclude.get()),'states':['pending']*len(self.paths),'errors':{}}
            self.persist_batch(out,batch)
        except OSError as exc:messagebox.showerror('保存位置不可用',str(exc));return
        self.out=out;self.settings['output_dir']=target;self.persist_settings();self.entries={};self.failures=[];self.current=None;self.viewport=None;self.current_id=None
        self.batch=batch;self.schedule_render()
        self.tree.delete(*self.tree.get_children())
        for i,path in enumerate(self.paths):self.tree.insert('','end',iid=str(i),text=Path(path).name,values=('','待分析'))
        self.start_queue()

    @staticmethod
    def persist_batch(out,batch):
        temp=Path(out)/'batch-session.tmp'
        temp.write_text(json.dumps(batch,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(Path(out)/'batch-session.json')

    def resume(self):
        if self.busy or self.saving or not self.batch:return
        if 'pending' not in self.batch['states']:return
        # Resume keeps the current review in memory; no image is replaced.
        self.start_queue()

    def start_queue(self):
        batch=self.batch;out=self.out;paths=list(batch['inputs']);exclude=batch['exclude_annotation']
        self.cancel.clear();self.busy=True;self.update_buttons()
        self.progress.configure(value=sum(s!='pending' for s in batch['states']),maximum=len(paths))
        existing=dict(self.entries)
        def worker():
            entries=existing;failures=[(paths[int(i)],err) for i,err in batch['errors'].items()];fatal=None
            try:
                for i,path in enumerate(paths):
                    if self.cancel.is_set():break
                    if batch['states'][i]!='pending':continue
                    self.events.put(('status',f'正在分析 {i+1}/{len(paths)}：{Path(path).name}'));dest=out/f'{i+1:03d}_{Path(path).stem}'
                    try:
                        row=save_one(path,dest,exclude);write_report([row],dest);entry={'row':row,'dest':str(dest)};entries[str(i)]=entry;batch['states'][i]='done';self.events.put(('result',(str(i),entry)))
                    except Exception as exc:
                        batch['states'][i]='failed';batch['errors'][str(i)]=str(exc);failures.append((path,str(exc)));self.events.put(('failure',(str(i),str(exc))))
                    self.persist_batch(out,batch)
                    self.events.put(('progress',sum(s!='pending' for s in batch['states'])))
                write_batch([entries[k] for k in sorted(entries,key=int)],failures,out)
            except Exception as exc:fatal=str(exc)
            finally:self.events.put(('done',(failures,fatal,self.cancel.is_set())))
        threading.Thread(target=worker,daemon=True).start()

    def stop(self):self.cancel.set();self.status.set('将在当前图片保存完成后停止；之后可点击“继续队列”处理剩余图片。')
    def retry(self):
        if self.busy or self.saving:return
        if self.batch:
            if 'failed' not in self.batch['states']:messagebox.showinfo('重试','当前没有失败图片。');return
            self.batch['states']=['pending' if s=='failed' else s for s in self.batch['states']];self.batch['errors']={};self.failures=[]
            for i,s in enumerate(self.batch['states']):
                if s=='pending':self.tree.set(str(i),'status','待分析')
            self.start_queue();return
        paths=[p for p,e in self.failures]
        if not paths:messagebox.showinfo('重试','当前没有失败图片。');return
        if self.set_paths(paths):self.run()

    def open_existing(self):
        if self.busy:return
        p=filedialog.askdirectory(title='选择批次或单张结果文件夹',initialdir=self.settings.get('output_dir',str(HOME)))
        if not p:return
        session=Path(p)/'batch-session.json'
        if session.exists():
            try:
                batch=json.loads(session.read_text(encoding='utf-8'));paths=batch['inputs']
                if not isinstance(paths,list) or not paths or not all(isinstance(x,str) for x in paths):raise ValueError('批次图片列表无效')
                states=batch.get('states',['pending']*len(paths));errors=batch.get('errors',{})
                if len(states)!=len(paths) or any(s not in ['pending','done','failed'] for s in states):raise ValueError('批次状态记录无效')
                entries={};exclude=batch.get('exclude_annotation')
                for i,path in enumerate(paths):
                    dest=Path(p)/f'{i+1:03d}_{Path(path).stem}';f=dest/f'{Path(path).stem}_result.json'
                    # Legacy sessions have only inputs; recover completed exports on disk.
                    if f.exists() and states[i]!='failed':
                        row=json.loads(f.read_text(encoding='utf-8'));entries[str(i)]={'row':row,'dest':str(dest)};states[i]='done'
                        if exclude is None:exclude=row.get('annotation_excluded')
                    elif states[i]=='done':states[i]='pending'
                if exclude is None:raise ValueError('旧批次没有保存分析设置且没有完成结果，无法确定原设置；请重新选择图片开始分析。')
                batch={'inputs':paths,'states':states,'errors':errors,'exclude_annotation':bool(exclude)}
            except Exception as exc:messagebox.showerror('无法恢复批次',str(exc));return
            if not self.set_paths(paths):return
            self.out=Path(p);self.batch=batch;self.entries=entries;self.failures=[(paths[int(i)],e) for i,e in errors.items()]
            self.exclude.set(batch['exclude_annotation'])
            for i,s in enumerate(states):
                e=entries.get(str(i));self.tree.item(str(i),values=(f'{e["row"]["area_percent"]:.2f}' if e else '',{'pending':'待继续','done':'可复核','failed':'失败'}[s]))
            self.progress.configure(value=sum(s!='pending' for s in states),maximum=len(paths));self.update_buttons()
            if entries:self.tree.selection_set(next(iter(entries)))
            self.status.set(f'已恢复批次：成功 {len(entries)} 张，待继续 {states.count("pending")} 张，失败 {states.count("failed")} 张。');return
        entries=[]
        for f in sorted(Path(p).rglob('*_result.json')):
            if 'revisions' in f.relative_to(p).parts:continue
            try:
                row=json.loads(f.read_text(encoding='utf-8'))
                if 'source' in row:entries.append({'row':row,'dest':str(f.parent)})
            except (OSError,ValueError):continue
        if not entries:messagebox.showinfo('未找到结果','此文件夹没有可打开的分析结果。');return
        if not self.set_paths([e['row']['source'] for e in entries]):return
        self.out=Path(p);self.tree.delete(*self.tree.get_children());self.entries={}
        for i,e in enumerate(entries):self.entries[str(i)]=e;self.tree.insert('','end',iid=str(i),text=e['row']['file'],values=(f'{e["row"]["area_percent"]:.2f}','可复核'))
        self.tree.selection_set('0');self.status.set(f'已打开 {len(entries)} 张结果，点击列表开始复核。')

    def select_result(self,event=None):
        sel=self.tree.selection()
        if not sel or sel[0]==self.current_id:return
        key=sel[0]
        if key not in self.entries:return
        if not self.guard_dirty():
            if self.current_id:self.tree.selection_set(self.current_id)
            return
        try:e=self.entries[key];state=Review(e['dest'],e['row'])
        except Exception as exc:messagebox.showerror('无法打开结果',str(exc));return
        self.current=state;self.viewport=Viewport(state.image);self.current_id=key;self.fit();self.update_summary()

    def update_summary(self):
        if not self.current:return
        r=self.current.stats();flag='未保存修改' if self.current.dirty else ('已保存复核' if self.current.saved else '自动结果')
        self.summary.set(f'{r["file"]}｜当前 {r["area_percent"]:.2f}%｜自动基线 {r["automatic_area_percent"]:.2f}%｜未决定候选 {r["uncertain_percent"]:.2f}%｜{flag}')
        self.tree.set(self.current_id,'area',f'{r["area_percent"]:.2f}');self.tree.set(self.current_id,'status',flag)

    def fit_scale(self):
        if not self.current:return 1.
        w,h=self.current.image.size
        return max(.001,min(min(max(1,c.winfo_width())/w,max(1,c.winfo_height())/h) for c in self.canvases))
    def fit(self):
        self.zoom=1.
        if self.current:self.center=(self.current.image.width/2,self.current.image.height/2)
        self.schedule_render()
    def actual(self):self.zoom=1/self.fit_scale();self.schedule_render()
    def change_zoom(self,factor):self.zoom=max(.5,min(64,self.zoom*factor));self.schedule_render()
    def coords(self,c,x,y):
        s=self.fit_scale()*self.zoom;return self.center[0]+(x-c.winfo_width()/2)/s,self.center[1]+(y-c.winfo_height()/2)/s
    def wheel(self,e,c):
        if not self.current:return
        before=self.coords(c,e.x,e.y);self.zoom=max(.5,min(64,self.zoom*(1.25 if e.delta>0 else .8)));after=self.coords(c,e.x,e.y)
        self.center=(self.center[0]+before[0]-after[0],self.center[1]+before[1]-after[1]);self.schedule_render()

    def press(self,e,c,force_pan=False):
        if not self.current:return
        mode='平移' if force_pan or c is self.canvases[0] else self.mode.get();point=self.coords(c,e.x,e.y)
        if self.saving and mode!='平移':return
        self.drag={'mode':mode,'screen':(e.x,e.y),'point':point,'start':point}
        if mode in ['加入','橙色转绿色','移除','确认排除']:self.current.snapshot();self.paint(point,mode);self.schedule_render()
    def paint(self,point,mode):
        try:r=max(1,min(200,int(self.radius.get())))
        except (ValueError,tk.TclError):r=12
        self.current.paint(*point,r,mode)
    def motion(self,e,c):
        if not self.drag or not self.current:return
        d=self.drag;mode=d['mode'];point=self.coords(c,e.x,e.y)
        if mode=='平移':
            s=self.fit_scale()*self.zoom;self.center=(self.center[0]-(e.x-d['screen'][0])/s,self.center[1]-(e.y-d['screen'][1])/s)
        elif mode=='替换排除框':
            c.delete('roi-drag');start=d['start'];s=self.fit_scale()*self.zoom
            c.create_rectangle((start[0]-self.center[0])*s+c.winfo_width()/2,(start[1]-self.center[1])*s+c.winfo_height()/2,e.x,e.y,outline='#ff8000',width=3,tags='roi-drag')
        else:
            x,y=d['point'];distance=math.hypot(point[0]-x,point[1]-y)
            try:spacing=max(1,int(self.radius.get())/2)
            except (ValueError,tk.TclError):spacing=6
            for t in np.linspace(0,1,max(1,math.ceil(distance/spacing))+1):self.paint((x+(point[0]-x)*t,y+(point[1]-y)*t),mode)
        d['point']=point;d['screen']=(e.x,e.y)
        if mode!='替换排除框':self.schedule_render()
    def release(self,e,c):
        if not self.drag:return
        d=self.drag;self.drag=None;c.delete('roi-drag')
        if d['mode']=='替换排除框':
            end=self.coords(c,e.x,e.y)
            if abs(end[0]-d['start'][0])>2 and abs(end[1]-d['start'][1])>2:
                try:self.current.rectangle((*d['start'],*end))
                except ValueError as exc:messagebox.showinfo('排除区域',str(exc))
        self.update_summary();self.schedule_render()

    def schedule_render(self):
        if self.render_job:return
        self.render_job=self.root.after(16,self.render)
    def render(self):
        self.render_job=None;self.photos=[]
        if not self.current:
            for c in self.canvases:c.delete('all');c.create_text(max(1,c.winfo_width())/2,max(1,c.winfo_height())/2,text='选择分析结果后显示',fill='white')
            return
        st=self.current;s=self.fit_scale()*self.zoom;w,h=st.image.size
        # Only crop the visible region; zoom does not build a giant resized image.
        for index,c in enumerate(self.canvases):
            cw=max(1,c.winfo_width());ch=max(1,c.winfo_height());c.delete('all')
            x0=max(0,int(self.center[0]-cw/(2*s)));y0=max(0,int(self.center[1]-ch/(2*s)));x1=min(w,math.ceil(self.center[0]+cw/(2*s)));y1=min(h,math.ceil(self.center[1]+ch/(2*s)))
            if x1<=x0 or y1<=y0:continue
            size=(max(1,round((x1-x0)*s)),max(1,round((y1-y0)*s)))
            im=self.viewport.draw(st,(x0,y0,x1,y1),size,self.view.get() if index else None)
            photo=ImageTk.PhotoImage(im);self.photos.append(photo);c.create_image((x0-self.center[0])*s+cw/2,(y0-self.center[1])*s+ch/2,image=photo,anchor='nw')
            if st.roi_bounds:
                bx,by,ex,ey=st.roi_bounds
                c.create_rectangle((bx-self.center[0])*s+cw/2,(by-self.center[1])*s+ch/2,(ex-self.center[0])*s+cw/2,(ey-self.center[1])*s+ch/2,outline='#ff8000',width=2,dash=(5,3))
    def undo(self):
        if self.current and not self.saving:self.current.undo();self.update_summary();self.schedule_render()
    def reset(self):
        if self.current and not self.saving:self.current.reset();self.update_summary();self.schedule_render()
    def save_review(self):
        if not self.current or self.saving:return False
        if self.busy:
            self.status.set('批次仍在处理，复核修改保留在当前窗口。请在分析结束后保存，避免与批次导出冲突。');return False
        self.drag=None;self.saving=True;self.update_buttons()
        self.status.set('正在后台保存高清结果，可继续缩放查看；保存完成后可继续编辑。')
        state=self.current;out=self.out;entries=list(self.entries.values());failures=list(self.failures)
        def worker():
            error=None;warning=None
            try:
                row,target=state.save()
                try:
                    if out:write_batch(entries,failures,out)
                except Exception as exc:warning=str(exc)+'\n单张结果：'+str(target)
            except Exception as exc:error=str(exc)
            self.events.put(('saved',(error,warning)))
        threading.Thread(target=worker,daemon=True).start()
        return True

    def poll(self):
        try:
            while True:
                try:kind,data=self.events.get_nowait()
                except queue.Empty:break
                try:
                    if kind=='status':self.status.set(data)
                    elif kind=='progress':self.progress.configure(value=data)
                    elif kind=='result':
                        key,entry=data;self.entries[key]=entry;self.tree.item(key,values=(f'{entry["row"]["area_percent"]:.2f}','可复核'))
                        if self.current is None:self.tree.selection_set(key);self.select_result()
                    elif kind=='failure':key,error=data;self.tree.item(key,values=('','失败'));self.status.set(error)
                    elif kind=='saved':
                        error,warning=data;self.saving=False;self.update_buttons();self.update_summary()
                        if error:
                            self.status.set('保存失败，修改仍保留，可以重试。');messagebox.showerror('复核保存失败',error)
                        elif warning:
                            self.status.set('单张复核已保存；批次汇总更新失败。');messagebox.showwarning('汇总未更新',warning)
                        else:self.status.set('已保存复核并更新报告，可继续编辑；如需切换图片或退出，请再次操作。')
                    elif kind=='done':
                        self.failures,fatal,stopped=data;self.busy=False;self.update_buttons();remaining=self.batch['states'].count('pending') if self.batch else 0;self.status.set(f'{"已停止" if stopped else "分析结束"}：成功 {len(self.entries)} 张，失败 {len(self.failures)} 张，待继续 {remaining} 张。')
                        if fatal:messagebox.showerror('批次汇总失败',fatal+'\n已完成图片保留，可重新打开结果文件夹。')
                        elif self.failures:messagebox.showwarning('部分图片失败','可点击“重试失败”。首项原因：'+self.failures[0][1])
                except Exception as exc:self.status.set('界面更新失败：'+str(exc))
        finally:self.root.after(100,self.poll)
    def open_report(self):
        if self.out and (self.out/'分析报告.html').exists():os.startfile(self.out/'分析报告.html')
        else:messagebox.showinfo('结果报告','请先完成分析或保存复核。')
    def open_folder(self):
        if self.out:os.startfile(self.out)
    def close(self):
        if self.saving:messagebox.showinfo('正在保存','请等待后台保存完成再退出。');return
        if self.busy:messagebox.showinfo('正在处理','请先停止队列，等待当前图片保存完成再退出。');return
        if self.guard_dirty():self.root.destroy()

def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:pass
    root=tk.Tk();App(root);root.mainloop()
