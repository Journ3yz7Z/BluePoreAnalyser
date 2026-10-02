"""Display-only image pyramid and viewport sampling; measurement masks stay original size."""
import numpy as np
from PIL import Image
from engine import display_image

class Viewport:
    def __init__(self, image):
        self.levels=[image]
        while self.levels[-1].width>800:
            im=self.levels[-1]
            self.levels.append(im.resize((max(1,im.width//2),max(1,im.height//2)),Image.Resampling.BOX))

    def draw(self,state,box,size,mode=None):
        x0,y0,x1,y1=box;ow,oh=self.levels[0].size
        scale=size[0]/(x1-x0)
        level=self.levels[0]
        for candidate in self.levels[1:]:
            if candidate.width/ow<scale:break
            level=candidate
        bounds=(x0*level.width/ow,y0*level.height/oh,x1*level.width/ow,y1*level.height/oh)
        im=level.resize(size,Image.Resampling.BILINEAR,box=bounds)
        if mode is None:return im
        # Sample only pixels required by the screen, at nearest-neighbor centers.
        xs=np.minimum(x1-1,x0+((np.arange(size[0])+.5)*(x1-x0)/size[0]).astype(int))
        ys=np.minimum(y1-1,y0+((np.arange(size[1])+.5)*(y1-y0)/size[1]).astype(int))
        ix=np.ix_(ys,xs);m=state.mask[ix];r=state.roi[ix]
        u=state.candidates[ix]&r&~m&~state.dismissed[ix] if mode!='只看计入区域' else np.zeros_like(m)
        return display_image(im,m,u,r,orange_only=mode=='只看橙色')
