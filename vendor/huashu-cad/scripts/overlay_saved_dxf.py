"""Register actual saved DXF geometry over a source image; no generator side-channel."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_overlay(spec_path, outdir):
    spec=json.loads(spec_path.read_text())
    base=spec_path.parent
    reference=(base/spec['reference_image']).resolve()
    source=Image.open(reference).convert('RGB')
    width,height=source.size
    mask=np.zeros((height,width),dtype=np.uint8)
    evidence={'reference':str(reference),'reference_sha256':digest(reference),
              'registration_spec':str(spec_path),'registration_sha256':digest(spec_path),
              'method':'read saved DXF -> ezdxf Frontend -> fixed image registration',
              'planes':[],'dimensions_or_text_not_included':True}
    for plane in spec['planes']:
        file=(base/plane['dxf']).resolve()
        matrix=np.array(plane['pixel_to_cad'],dtype=float)
        if matrix.shape!=(3,3) or not np.allclose(matrix[2],[0,0,1]):
            raise ValueError('Expected a 3x3 affine pixel_to_cad matrix')
        if not np.allclose([matrix[0,1],matrix[1,0]],[0,0]):
            raise ValueError('This tested renderer supports calibrated axis scales and translation only')
        sx,sy=matrix[0,0],matrix[1,1]
        if sx<=0 or sy>=0:raise ValueError('Expected image-right X and image-down negative CAD Y')
        doc=ezdxf.readfile(file)
        included=set(plane['include_layers'])
        fig=plt.figure(figsize=(width/100,height/100),dpi=100)
        ax=fig.add_axes([0,0,1,1])
        config=Configuration(background_policy=BackgroundPolicy.WHITE,color_policy=ColorPolicy.BLACK)
        Frontend(RenderContext(doc),MatplotlibBackend(ax),config=config).draw_layout(
            doc.modelspace(),finalize=True,
            filter_func=lambda e:e.dxf.layer in included and e.dxftype() not in ('TEXT','MTEXT','DIMENSION'))
        ax.set_xlim(matrix[0,2],sx*width+matrix[0,2])
        ax.set_ylim(sy*height+matrix[1,2],matrix[1,2])
        ax.set_aspect('auto')
        ax.set_axis_off()
        # The ezdxf backend changes figure size on finalize; restore the locked pixel canvas.
        fig.set_size_inches(width/100,height/100,forward=True)
        fig.set_dpi(100)
        ax.set_position([0,0,1,1])
        fig.canvas.draw()
        rgba=np.asarray(fig.canvas.buffer_rgba())
        actual=255-rgba[:,:,:3].min(axis=2)
        mask=np.maximum(mask,actual)
        plt.close(fig)
        evidence['planes'].append({'name':plane['name'],'dxf':str(file),'dxf_sha256':digest(file),
           'pixel_to_cad':matrix.tolist(),'layers':sorted(included),
           'selected_entity_count':sum(e.dxf.layer in included and e.dxftype() not in ('TEXT','MTEXT','DIMENSION') for e in doc.modelspace())})
    outdir.mkdir(parents=True,exist_ok=True)
    native=Image.fromarray(np.repeat((255-mask)[:,:,None],3,axis=2))
    native.save(outdir/'saved_dxf_registered.png')
    rgb=np.array(source,dtype=float)
    alpha=(mask/255*0.78)[:,:,None]
    colored=rgb*(1-alpha)+np.array([218,35,52])[None,None,:]*alpha
    Image.fromarray(np.uint8(np.clip(colored,0,255))).save(outdir/'saved_dxf_overlay.png')
    comparison=Image.new('RGB',(2*width,height),'white')
    comparison.paste(source,(0,0));comparison.paste(native,(width,0))
    comparison.save(outdir/'saved_dxf_comparison.png')
    evidence['overlay_sha256']=digest(outdir/'saved_dxf_overlay.png')
    (outdir/'saved_dxf_overlay_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(outdir),'planes':len(evidence['planes']),
                      'selected_entities':sum(p['selected_entity_count'] for p in evidence['planes'])},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('spec',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();make_overlay(a.spec,a.out)
