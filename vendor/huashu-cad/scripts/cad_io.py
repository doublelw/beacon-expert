"""Inspect saved CAD files and render DXF. Observations, not task approval."""
import argparse
import hashlib
import importlib.metadata
import json
import shutil
from pathlib import Path


def doctor():
    result = {}
    for name in ('ezdxf', 'matplotlib', 'build123d', 'py-gearworks', 'bd_warehouse', 'playwright'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    import sys
    result['python'] = sys.version.split()[0]
    result['ffmpeg'] = shutil.which('ffmpeg')
    return {'libraries': result, 'native_cad_verified': False}


def inspect_file(path):
    if path.suffix.lower() in ('.step', '.stp'):
        from build123d import import_step
        shape = import_step(str(path))
        box = shape.bounding_box()
        return {'path': str(path), 'format': 'STEP', 'valid': shape.is_valid,
                'solids': len(shape.solids()), 'volume': shape.volume,
                'bbox': {'min': list(box.min), 'max': list(box.max)}}
    if path.suffix.lower() != '.dxf':
        raise ValueError('Only DXF and STEP are parsed; DWG requires a native backend.')
    import ezdxf
    from ezdxf import bbox
    doc = ezdxf.readfile(path)
    audit = doc.audit()
    entities = []
    for e in doc.modelspace():
        item = {'handle': e.dxf.handle, 'type': e.dxftype(), 'layer': e.dxf.layer}
        for key in ('start', 'end', 'center', 'radius', 'insert', 'name', 'text'):
            if e.dxf.hasattr(key):
                v = e.dxf.get(key)
                item[key] = list(v) if not isinstance(v, (str, int, float)) else v
        if e.dxftype() == 'LWPOLYLINE':
            item['points'] = [list(v) for v in e.get_points('xyb')]
            item['closed'] = e.closed
        if e.dxftype() == 'INSERT':
            item['attributes'] = {a.dxf.tag: a.dxf.text for a in e.attribs}
        if e.dxftype() == 'DIMENSION':
            measurement = e.get_measurement()
            try:
                item['measurement'] = float(measurement)
            except (TypeError, ValueError):
                item['measurement'] = str(measurement)
            style = e.override()
            item['dimension_style'] = e.dxf.dimstyle
            item['display_factor'] = style.get('dimlfac', 1)
            item['text_override'] = e.dxf.get('text', '<>')
            item['rendered_text'] = [
                v.plain_text() if v.dxftype() == 'MTEXT' else v.dxf.text
                for v in e.virtual_entities() if v.dxftype() in ('TEXT', 'MTEXT')]
            item['needs_display_review'] = (
                item['display_factor'] != 1 or item['text_override'] not in ('', '<>'))
            item['display_settings'] = {
                k: style.get(k) for k in ('dimtxt','dimasz','dimscale','dimdec','dimpost','dimtxsty')}
            item['acad_xdata'] = (
                [[tag.code,str(tag.value)] for tag in e.get_xdata('ACAD')]
                if e.has_xdata('ACAD') else [])
        bounds = bbox.extents([e], fast=True)
        if bounds.has_data:
            item['bbox'] = {'min': list(bounds.extmin), 'max': list(bounds.extmax)}
        entities.append(item)
    return {'path': str(path), 'format': 'DXF', 'units': doc.units,
            'version': doc.dxfversion, 'audit_errors': [str(x) for x in audit.errors],
            'audit_fixes': [str(x) for x in audit.fixes],
            'layers': [l.dxf.name for l in doc.layers], 'entities': entities}


def render(path, out, layers=None, viewbox=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import ezdxf
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    doc = ezdxf.readfile(path)
    fig = plt.figure(figsize=(14, 10))
    ax = fig.add_axes([0.03, 0.03, 0.94, 0.94])
    config = Configuration(background_policy=BackgroundPolicy.WHITE,
                           color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=config).draw_layout(
        doc.modelspace(), finalize=True,
        filter_func=(lambda e: e.dxf.layer in layers) if layers else None)
    if viewbox:
        x1, y1, x2, y2 = viewbox
        if x1 >= x2 or y1 >= y2:
            raise ValueError('bbox requires xmin < xmax and ymin < ymax')
        ax.set_xlim(x1, x2)
        ax.set_ylim(y1, y2)
    ax.set_aspect('equal')
    fig.savefig(out, dpi=160, facecolor='white')
    plt.close(fig)
    return {'preview': str(out), 'renderer': 'ezdxf+matplotlib',
            'layers': layers, 'bbox': viewbox, 'cad_modified': False}


def preserve(path, directory):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{path.stem}_{digest[:12]}{path.suffix}'
    if not target.exists():
        shutil.copy2(path, target)
    return {'preserved': str(target), 'sha256': digest}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['doctor', 'inspect', 'render', 'preserve'])
    p.add_argument('path', nargs='?', type=Path)
    p.add_argument('--out', type=Path)
    p.add_argument('--layers', nargs='+')
    p.add_argument('--bbox', nargs=4, type=float)
    args = p.parse_args()
    if args.command == 'doctor':
        result = doctor()
    elif not args.path:
        p.error('path required')
    elif args.command == 'inspect':
        result = inspect_file(args.path)
    elif not args.out:
        p.error('--out required for render or preserve')
    elif args.command == 'preserve':
        result = preserve(args.path, args.out)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        result = render(args.path, args.out, args.layers, args.bbox)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out and args.command == 'inspect':
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized)
    print(serialized)


if __name__ == '__main__':
    main()
