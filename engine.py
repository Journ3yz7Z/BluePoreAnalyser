"""Standalone GeoSlicer Bayesian pore segmentation with conservative edge rejection."""
from pathlib import Path
import json, csv, html
import numpy as np
from PIL import Image, ImageFilter, ImageDraw
from vendor.geoslicer_bayes import bayesian_inference

HOME=Path(__file__).resolve().parent
VERSION='0.2.0'

def log_probabilities(hsv,mean,cov,tile_rows=80):
    h,w=hsv.shape[:2]
    logp=np.empty((len(mean),h,w),dtype=np.float32)
    for y in range(0,h,tile_rows):
        y1=min(h,y+tile_rows); lo=max(0,y-1); hi=min(h,y1+1)
        tile=hsv[lo:hi]
        scores=np.zeros((len(mean),len(tile),w))
        for channel in range(3):
            scores+=bayesian_inference(tile[:,:,channel],range(len(mean)),mean[:,channel],cov[:,channel],1,'cubes',True)
        logp[:,y:y1]=scores[:,y-lo:y1-lo]
    return logp

def segment(image, exclude_annotation=True, max_width=1600):
    image=image.convert('RGB')
    size=image.size
    work=image.copy(); work.thumbnail((max_width,max_width*2),Image.Resampling.LANCZOS)
    rgb=np.asarray(work).astype(np.float32)
    hsv=np.asarray(work.convert('HSV')).astype(np.float32)
    h,w=rgb.shape[:2]
    with np.load(HOME/'model.npz',allow_pickle=False) as model:
        mean=model['mean']; cov=model['cov']; pore_classes=model['pore_classes']
    # Tile with a 1-pixel halo, matching upstream 3x3 neighborhoods exactly.
    logp=log_probabilities(hsv,mean,cov)
    classes=np.argmax(logp,axis=0)
    # Color gate prevents Gaussian extrapolation into white/brown phases.
    hue=hsv[:,:,0]*360/255; sat=hsv[:,:,1]/255
    score=(rgb[:,:,2]-rgb[:,:,0])/(rgb[:,:,2]+rgb[:,:,0]+1)
    candidate=(hue>=185)&(hue<=255)&(sat>=.22)&(score>=.18)
    learned=np.isin(classes,pore_classes)&candidate
    # A blue region must retain blue chromaticity after neighborhood averaging.
    smooth=np.asarray(work.filter(ImageFilter.GaussianBlur(2))).astype(np.float32)
    broad=(smooth[:,:,2]-smooth[:,:,0])/(smooth[:,:,2]+smooth[:,:,0]+1)
    density=np.asarray(Image.fromarray((learned*255).astype('uint8')).filter(ImageFilter.BoxBlur(5)))
    core=learned&(broad>=.28)&(density>=204)
    core_image=Image.fromarray((core*255).astype('uint8'))
    nearby=np.asarray(core_image.filter(ImageFilter.MaxFilter(11)))>0
    small_mask=learned&nearby
    uncertain=candidate&~small_mask
    roi=np.ones((size[1],size[0]),bool)
    x=int(size[0]*.91); y=int(size[1]*.91)
    if exclude_annotation: roi[y:,x:]=False
    # All saved masks use original dimensions; inference was resized as recorded.
    def full(m): return np.asarray(Image.fromarray((m*255).astype('uint8')).resize(size,Image.Resampling.NEAREST))>0
    mask=full(small_mask)&roi; unsure=full(uncertain)&roi
    den=int(roi.sum()); count=int(mask.sum())
    gray=np.asarray(work.convert('L')).astype(float)
    sharpness=float((np.diff(gray,axis=0)**2).mean()+(np.diff(gray,axis=1)**2).mean())
    result={'version':VERSION,'original_width':size[0],'original_height':size[1],'inference_width':w,'inference_height':h,'blue_pixels':count,'roi_pixels':den,'area_percent':count/den*100,'uncertain_pixels':int(unsure.sum()),'uncertain_percent':float(unsure.sum()/den*100),'annotation_excluded':bool(exclude_annotation),'excluded_rectangle':[x,y,*size] if exclude_annotation else None,'sharpness_score':sharpness,'model':'GeoSlicer Bayesian 3x3; project reference parameters; not official pretrained weights','requires_review':True}
    return result,mask,unsure,roi

def overlays(image,mask,uncertain,roi):
    a=np.array(image.convert('RGB')); overlay=a.copy()
    overlay[mask]=(a[mask]*.4+np.array([20,235,75])*.6).astype('uint8')
    overlay[~roi]=(a[~roi]*.25).astype('uint8')
    review=overlay.copy(); review[uncertain&roi&~mask]=[255,128,0]
    return Image.fromarray(overlay),Image.fromarray(review)

def display_image(image,mask,uncertain,roi,size=None,orange_only=False):
    """Paint AFTER resizing, avoiding color blending/compression in review previews."""
    im=image.convert('RGB').copy()
    if size: im.thumbnail(size,Image.Resampling.LANCZOS)
    def scaled(m):
        if m.shape==(im.height,im.width):return m
        return np.asarray(Image.fromarray((m*255).astype('uint8')).resize(im.size,Image.Resampling.NEAREST))>0
    a=np.array(im); m=scaled(mask); u=scaled(uncertain); r=scaled(roi)
    if orange_only:
        a=np.repeat(np.asarray(im.convert('L'))[:,:,None],3,axis=2)
    else:
        a[m&r]=(a[m&r]*.35+np.array([20,235,75])*.65).astype('uint8')
    a[u&r&~m]=[255,128,0]
    a[~r]=(a[~r]*.2).astype('uint8')
    return Image.fromarray(a)

def export_views(image,mask,uncertain,roi,out,stem):
    out=Path(out)
    for suffix,inc_u in [('overlay',False),('review',True)]:
        u=uncertain if inc_u else np.zeros_like(mask)
        display_image(image,mask,u,roi).save(out/f'{stem}_{suffix}.png')
        name='review_preview' if inc_u else 'preview'
        display_image(image,mask,u,roi,(1000,700)).save(out/f'{stem}_{name}.png')
    display_image(image,mask,uncertain,roi,(1000,700),True).save(out/f'{stem}_orange.png')

def save_one(path,out,exclude_annotation=True):
    path=Path(path); out=Path(out); out.mkdir(parents=True,exist_ok=True)
    with Image.open(path) as src: image=src.convert('RGB')
    result,full_mask,full_uncertain,roi=segment(image,False)
    if exclude_annotation:
        roi[int(image.height*.91):,int(image.width*.91):]=False
    mask=full_mask&roi; uncertain=full_uncertain&roi
    result.update(annotation_excluded=bool(exclude_annotation),excluded_rectangle=[int(image.width*.91),int(image.height*.91),*image.size] if exclude_annotation else None,blue_pixels=int(mask.sum()),roi_pixels=int(roi.sum()),area_percent=float(mask.sum()/roi.sum()*100),uncertain_pixels=int(uncertain.sum()),uncertain_percent=float(uncertain.sum()/roi.sum()*100))
    result.update(file=path.name,source=str(path.resolve()))
    for suffix,m in [('auto_full',full_mask),('candidate_full',full_uncertain)]:
        Image.fromarray((m*255).astype('uint8')).save(out/f'{path.stem}_{suffix}.png')
    export_views(image,mask,uncertain,roi,out,path.stem)
    overlay,review=overlays(image,mask,uncertain,roi)
    for suffix,m in [('mask',mask),('uncertain',uncertain),('roi',roi)]:
        Image.fromarray((m*255).astype('uint8')).save(out/f'{path.stem}_{suffix}.png')
    overlay.save(out/f'{path.stem}_overlay.jpg',quality=95)
    review.save(out/f'{path.stem}_review.jpg',quality=95)
    image.thumbnail((1000,700)); image.save(out/f'{path.stem}_original.jpg',quality=94)
    for pic,name in [(overlay,'preview'),(review,'review_preview')]:
        pic.thumbnail((1000,700)); pic.save(out/f'{path.stem}_{name}.jpg',quality=94)
    (out/f'{path.stem}_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

from reporting import write_report
