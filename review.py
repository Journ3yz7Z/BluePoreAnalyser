"""Review state uses original pixel coordinates; automatic masks remain immutable."""
from pathlib import Path
import json,datetime,uuid,shutil
import numpy as np
from PIL import Image
from engine import export_views
from reporting import write_report

class Review:
    def __init__(self,dest,row):
        self.dest=Path(dest);self.row=dict(row);self.stem=Path(row['file']).stem
        with Image.open(row['source']) as im:self.image=im.convert('RGB')
        def read(s):return np.array(Image.open(self.dest/f'{self.stem}_{s}.png'))>0
        self.initial_roi=read('roi');self.initial_mask=read('mask')
        if self.initial_mask.shape!=(self.image.height,self.image.width):
            raise ValueError('原图尺寸与分析掩膜不一致。请恢复分析时的原图，或重新分析。')
        self.roi=self.initial_roi.copy();self.mask=self.initial_mask.copy()
        self.can_expand_roi=(self.dest/f'{self.stem}_auto_full.png').exists()
        self.auto=read('auto_full') if self.can_expand_roi else self.initial_mask.copy()
        self.candidates=read('candidate_full') if self.can_expand_roi else read('uncertain')
        self.dismissed=np.zeros_like(self.mask)
        self.history=[];self.dirty=False;self.saved=False
        pointer=self.dest/'current_review.json'
        if pointer.exists():
            rev=self.dest/json.loads(pointer.read_text(encoding='utf-8'))['revision']
            for attr in ['mask','roi','dismissed']:
                setattr(self,attr,np.array(Image.open(rev/f'{self.stem}_{attr}.png'))>0)
            self.saved=True
        self.recount()

    def contributions(self,sl):
        m=self.mask[sl];r=self.roi[sl];a=self.auto[sl];c=self.candidates[sl];d=self.dismissed[sl]
        return np.array([np.count_nonzero(m&r),np.count_nonzero(c&r&~m&~d),np.count_nonzero(m&~a&r),np.count_nonzero(a&~m&r),np.count_nonzero(d&c&r&~m)],dtype=np.int64)

    def recount(self):
        self.counts=self.contributions(np.s_[:,:]);self.den=int(np.count_nonzero(self.roi));self.auto_count=int(np.count_nonzero(self.auto&self.roi))
        yy=np.flatnonzero((~self.roi).any(axis=1));xx=np.flatnonzero((~self.roi).any(axis=0))
        self.roi_bounds=(int(xx[0]),int(yy[0]),int(xx[-1])+1,int(yy[-1])+1) if len(xx) else None

    def snapshot(self):
        self.history.append(tuple(np.packbits(getattr(self,k)) for k in ['mask','roi','dismissed']))
        self.history=self.history[-20:]

    def undo(self):
        if not self.history:return
        n=self.mask.size;shape=self.mask.shape
        for key,bits in zip(['mask','roi','dismissed'],self.history.pop()):
            setattr(self,key,np.unpackbits(bits,count=n).reshape(shape).astype(bool))
        self.dirty=True
        self.recount()

    @property
    def pending(self):return self.candidates&self.roi&~self.mask&~self.dismissed

    def paint(self,x,y,radius,mode):
        h,w=self.mask.shape;x=int(x);y=int(y);r=max(1,int(radius))
        x0=max(0,x-r);x1=min(w,x+r+1);y0=max(0,y-r);y1=min(h,y+r+1)
        if x1<=x0 or y1<=y0:return
        sl=np.s_[y0:y1,x0:x1];before=self.contributions(sl)
        yy,xx=np.ogrid[y0:y1,x0:x1];hit=(xx-x)**2+(yy-y)**2<=r*r
        hit &= self.roi[y0:y1,x0:x1]
        m=self.mask[y0:y1,x0:x1];d=self.dismissed[y0:y1,x0:x1]
        if mode=='加入':m[hit]=True;d[hit]=False
        elif mode=='橙色转绿色':
            selected=hit&self.candidates[sl]&~m&~d
            m[selected]=True
        elif mode=='移除':m[hit]=False;d[hit]=True
        elif mode=='确认排除':d[hit&~m]=True
        self.dirty=True
        self.counts+=self.contributions(sl)-before

    def rectangle(self,box):
        h,w=self.mask.shape;x0,y0,x1,y1=map(int,box)
        x0,x1=sorted([max(0,min(w,x0)),max(0,min(w,x1))]);y0,y1=sorted([max(0,min(h,y0)),max(0,min(h,y1))])
        base=np.ones_like(self.roi) if self.can_expand_roi else self.initial_roi.copy()
        base[y0:y1,x0:x1]=False
        if not base.any():raise ValueError('不能排除整幅图片，请保留有效分析区域。')
        self.snapshot(); old=self.roi.copy();self.roi=base
        self.mask=(self.mask&base)|(self.auto&base&~old)
        self.dismissed &= base;self.dirty=True
        self.recount()

    def reset(self):
        self.snapshot();self.mask=self.initial_mask.copy();self.roi=self.initial_roi.copy();self.dismissed[:]=False;self.dirty=True
        self.recount()

    def stats(self):
        den=self.den;num,p,added,removed,dismissed=map(int,self.counts)
        result=dict(self.row)
        result.update(area_percent=num/den*100,automatic_area_percent=self.auto_count/den*100,original_automatic_area_percent=self.row['area_percent'],roi_pixels=den,blue_pixels=num,uncertain_pixels=p,uncertain_percent=p/den*100,review_status='人工复核记录（仍有未决定候选）' if p else '人工复核记录（候选均已处理）',added_pixels=added,removed_pixels=removed,confirmed_excluded_pixels=dismissed)
        return result

    def save(self):
        revision=Path('revisions')/(datetime.datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        target=self.dest/revision;target.mkdir(parents=True)
        row=self.stats();row['reviewed_at']=datetime.datetime.now().isoformat(timespec='seconds')
        row['review_version']='0.2.0';row['roi_manually_changed']=not np.array_equal(self.roi,self.initial_roi)
        row['initial_excluded_rectangle']=self.row.get('excluded_rectangle')
        row['excluded_rectangle']=None
        row['annotation_excluded']=bool((~self.roi).any())
        row['effective_roi_mask']=f'{self.stem}_roi.png'
        pending=self.pending
        for name,m in [('mask',self.mask&self.roi),('roi',self.roi),('dismissed',self.dismissed),('uncertain',pending)]:
            Image.fromarray((m*255).astype('uint8')).save(target/f'{self.stem}_{name}.png')
        export_views(self.image,self.mask,pending,self.roi,target,self.stem)
        pic=self.image.copy();pic.thumbnail((1000,700));pic.save(target/f'{self.stem}_original.jpg',quality=95)
        (target/'review.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
        write_report([row],target,'人工复核结果')
        # Commit pointer only after every review artifact was written successfully.
        temp=self.dest/'current_review.tmp';temp.write_text(json.dumps({'revision':revision.as_posix()}),encoding='utf-8');temp.replace(self.dest/'current_review.json')
        self.dirty=False;self.saved=True
        return row,target
