"""Readable reports shared by automatic and manually reviewed results."""
from pathlib import Path
from urllib.parse import quote
import csv,html,json,shutil

STYLE='''body{font:18px/1.6 "Microsoft YaHei",system-ui;margin:24px auto;max-width:1600px;padding:0 20px;background:#f4f6f8;color:#203040}section{background:white;padding:20px;margin:20px 0;border-radius:12px}.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}figure{margin:0}img{width:100%;cursor:zoom-in}figcaption{padding:8px}b{color:#087b50}a{color:#0065a8}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ccd3db}details{margin:12px 0}.orange{color:#ab4c00}'''

def write_report(rows,out,title='蓝色孔隙面积分析'):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    fields=['file','area_percent','automatic_area_percent','uncertain_percent','review_status','blue_pixels','roi_pixels','source']
    with (out/'面积统计.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    cards=[]
    for r in rows:
        stem=Path(r['file']).stem
        def uri(suffix): return quote(stem+suffix)
        figs=[]
        for small,large,label in [('_original.jpg','_original.jpg','原图'),('_preview.png','_overlay.png','绿色：当前计入面积'),('_review_preview.png','_review.png','橙色：尚未作出决定的蓝色候选')]:
            if not (out/(stem+small)).exists(): small=small.replace('.png','.jpg');large=large.replace('.png','.jpg')
            figs.append(f'<figure><a target="_blank" href="{uri(large)}"><img src="{uri(small)}"></a><figcaption>{label} · 点击放大</figcaption></figure>')
        orange=''
        if (out/(stem+'_orange.png')).exists():
            orange=f'<details><summary class="orange">只看橙色候选（灰度背景）</summary><a target="_blank" href="{uri("_review.png")}"><img style="max-width:1000px" src="{uri("_orange.png")}"></a></details>'
        auto=r.get('automatic_area_percent',r['area_percent'])
        cards.append(f'<section><h2>{html.escape(r["file"])}</h2><p>当前面积率 <b>{r["area_percent"]:.2f}%</b> · 自动基线 {auto:.2f}% · 未决定候选 {r["uncertain_percent"]:.2f}%</p><p>{html.escape(r.get("review_status","尚未人工复核"))}。未决定候选不计入面积，候选占比不是误差范围。</p><div class="pair">'+''.join(figs)+'</div>'+orange+'</section>')
    content='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+STYLE+'</style><h1>'+html.escape(title)+'</h1><p>绿色＝计入面积；亮橙色＝算法排除、尚未由人工决定的蓝色候选。人工复核请在桌面程序中补选、移除或确认排除，然后保存。</p><p>图像可点击放大，浏览器 Ctrl＋加号可放大文字。分母为有效视场，包含骨料与基体。</p><details><summary>计算说明</summary><p>GeoSlicer 贝叶斯内核及项目参考参数；未使用官方预训练权重。自动推断宽度最大1600像素，细孔可能漏检。人工画笔在原图坐标上修改掩膜。结果仍需实验人员核查，不因人工保存而自动成为已验证孔隙率。</p></details>'+''.join(cards)+'</html>'
    (out/'分析报告.html').write_text(content,encoding='utf-8');return out/'分析报告.html'

def write_batch(entries,failures,out):
    out=Path(out); results=[];links=[]
    if any((Path(e['dest'])/'current_review.json').exists() for e in entries):
        for name in ['分析报告.html','批次结果.json','批次面积统计.csv','面积统计.csv']:
            old=out/name;backup=out/('自动结果备份_'+name)
            if old.exists() and not backup.exists():shutil.copyfile(old,backup)
    for entry in entries:
        row=entry['row']; dest=Path(entry['dest']);stem=Path(row['file']).stem
        pointer=dest/'current_review.json'
        if pointer.exists():
            rev=dest/json.loads(pointer.read_text(encoding='utf-8'))['revision']
            row=json.loads((rev/'review.json').read_text(encoding='utf-8'));dest=rev
        results.append(row)
        path=quote((dest/'分析报告.html').relative_to(out).as_posix())
        links.append(f'<tr><td><a href="{path}">{html.escape(row["file"])}</a></td><td>{row["area_percent"]:.2f}%</td><td>{html.escape(row.get("review_status","未复核"))}</td></tr>')
    (out/'批次结果.json').write_text(json.dumps({'results':results,'failures':failures},ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'批次面积统计.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['原图','当前面积率(%)','自动基线(%)','未决定候选(%)','复核状态'])
        writer.writerows([r['source'],r['area_percent'],r.get('automatic_area_percent',r['area_percent']),r['uncertain_percent'],r.get('review_status','未复核')] for r in results)
    errors=''.join('<li>'+html.escape(str(p)+': '+str(e))+'</li>' for p,e in failures)
    (out/'分析报告.html').write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+STYLE+'</style><h1>批次分析结果</h1><table><tr><th>图片（点击查看）</th><th>当前面积率</th><th>状态</th></tr>'+''.join(links)+'</table><h2>失败项</h2><ul>'+errors+'</ul>',encoding='utf-8')
