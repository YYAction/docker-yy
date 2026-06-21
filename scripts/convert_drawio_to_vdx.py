"""
将 draw.io (.drawio) 转换为 Visio VDX 格式（单个 XML 文件，Visio 2003-2013 兼容）。
比 .vsdx 简单得多，直接是一个 XML 文件。
"""

import xml.etree.ElementTree as ET
import re
import html as html_module
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def strip_html(text: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<div[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    return html_module.unescape(text).strip()


def xml_escape(text: str) -> str:
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def parse_fill_color(style: str) -> str:
    """从 draw.io style 字符串里提取 fillColor，找不到就用白色。"""
    m = re.search(r'fillColor=([^;]+)', style or '')
    if m:
        color = m.group(1).strip()
        if color.lower() != 'none' and color.startswith('#'):
            return color
    return '#ffffff'


def parse_stroke_color(style: str) -> str:
    m = re.search(r'strokeColor=([^;]+)', style or '')
    if m:
        color = m.group(1).strip()
        if color.lower() != 'none' and color.startswith('#'):
            return color
    return '#000000'


def is_rounded(style: str) -> bool:
    return 'rounded=1' in (style or '')


# ---------------------------------------------------------------------------
# 解析 draw.io
# ---------------------------------------------------------------------------

def parse_drawio(drawio_path: str):
    """返回 [(page_name, page_w_px, page_h_px, shapes), ...]"""
    tree = ET.parse(drawio_path)
    root = tree.getroot()
    pages = []

    for diagram in root.findall('diagram'):
        name = diagram.get('name', 'Page-1')
        model = diagram.find('mxGraphModel')
        if model is None:
            continue

        page_w = float(model.get('pageWidth', 2200))
        page_h = float(model.get('pageHeight', 1800))

        shapes = []
        root_el = model.find('root')
        if root_el is None:
            continue

        for cell in root_el.findall('mxCell'):
            cid = cell.get('id', '')
            if cid in ('0', '1') or cell.get('vertex') != '1':
                continue
            geo = cell.find('mxGeometry')
            if geo is None:
                continue

            shapes.append({
                'x':      float(geo.get('x', 0)),
                'y':      float(geo.get('y', 0)),
                'w':      float(geo.get('width', 100)),
                'h':      float(geo.get('height', 40)),
                'label':  strip_html(cell.get('value', '')),
                'style':  cell.get('style', ''),
            })

        pages.append((name, page_w, page_h, shapes))

    return pages


# ---------------------------------------------------------------------------
# 生成 VDX
# ---------------------------------------------------------------------------

def shapes_to_vdx(shapes, page_h_in: float, dpi: float = 96.0) -> str:
    parts = []
    for i, s in enumerate(shapes, start=1):
        pin_x  = (s['x'] + s['w'] / 2) / dpi
        pin_y  = page_h_in - (s['y'] + s['h'] / 2) / dpi
        width  = s['w'] / dpi
        height = s['h'] / dpi
        rounding = '0.0417' if is_rounded(s['style']) else '0'
        fill   = parse_fill_color(s['style'])
        stroke = parse_stroke_color(s['style'])
        label  = xml_escape(s['label'])

        parts.append(f'''\
        <Shape ID="{i}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">
          <XForm>
            <PinX>{pin_x:.5f}</PinX>
            <PinY>{pin_y:.5f}</PinY>
            <Width>{width:.5f}</Width>
            <Height>{height:.5f}</Height>
            <LocPinX F="Width*0.5">{width/2:.5f}</LocPinX>
            <LocPinY F="Height*0.5">{height/2:.5f}</LocPinY>
            <Angle>0</Angle>
            <FlipX>0</FlipX>
            <FlipY>0</FlipY>
          </XForm>
          <Fill>
            <FillForegnd>{fill}</FillForegnd>
            <FillBkgnd>#ffffff</FillBkgnd>
            <FillPattern>1</FillPattern>
          </Fill>
          <Line>
            <LineWeight>0.01</LineWeight>
            <LineColor>{stroke}</LineColor>
            <LinePattern>1</LinePattern>
            <Rounding>{rounding}</Rounding>
          </Line>
          <Geom IX="0">
            <MoveTo IX="1"><X F="Width*0">0</X><Y F="Height*0">0</Y></MoveTo>
            <LineTo IX="2"><X F="Width*1">{width:.5f}</X><Y F="Height*0">0</Y></LineTo>
            <LineTo IX="3"><X F="Width*1">{width:.5f}</X><Y F="Height*1">{height:.5f}</Y></LineTo>
            <LineTo IX="4"><X F="Width*0">0</X><Y F="Height*1">{height:.5f}</Y></LineTo>
            <LineTo IX="5"><X F="Width*0">0</X><Y F="Height*0">0</Y></LineTo>
          </Geom>
          <Text>{label}</Text>
        </Shape>''')

    return '\n'.join(parts)


def convert(drawio_path: str, vdx_path: str):
    pages = parse_drawio(drawio_path)
    if not pages:
        print('未找到任何图页，转换终止。')
        return

    DPI = 96.0
    pages_xml = []

    for pid, (pname, pw_px, ph_px, shapes) in enumerate(pages):
        ph_in = ph_px / DPI
        pw_in = pw_px / DPI
        shapes_vdx = shapes_to_vdx(shapes, ph_in, DPI)

        pages_xml.append(f'''\
    <Page ID="{pid}" NameU="{xml_escape(pname)}" Name="{xml_escape(pname)}">
      <PageSheet LineStyle="0" FillStyle="0" TextStyle="0">
        <PageProps>
          <PageWidth>{pw_in:.5f}</PageWidth>
          <PageHeight>{ph_in:.5f}</PageHeight>
          <PageScale>1</PageScale>
          <DrawingScale>1</DrawingScale>
        </PageProps>
      </PageSheet>
      <Shapes>
{shapes_vdx}
      </Shapes>
    </Page>''')

    all_pages = '\n'.join(pages_xml)

    vdx_content = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VisioDocument xmlns="http://schemas.microsoft.com/visio/2003/core"
               xmlns:vx="http://schemas.microsoft.com/visio/2006/extension"
               start="1" end="2">
  <DocumentProperties>
    <Creator>draw.io Converter</Creator>
    <Title>{xml_escape(Path(drawio_path).stem)}</Title>
  </DocumentProperties>
  <DocumentSettings/>
  <Colors/>
  <Fonts>
    <Font ID="0" Name="Arial" CharSet="0" Unicode="1"/>
    <Font ID="1" Name="Microsoft YaHei" CharSet="134" Unicode="1"/>
  </Fonts>
  <Masters/>
  <Pages>
{all_pages}
  </Pages>
</VisioDocument>'''

    Path(vdx_path).write_text(vdx_content, encoding='utf-8')

    total = sum(len(p[3]) for p in pages)
    print(f'转换完成：{len(pages)} 个页面，共 {total} 个形状')
    print(f'输出文件：{vdx_path}')


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else '/home/yyaction/航空安保一体化平台拓扑图.drawio'
    dst = sys.argv[2] if len(sys.argv) > 2 else str(Path(src).with_suffix('.vdx'))
    convert(src, dst)
