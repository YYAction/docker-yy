"""
将 draw.io (.drawio) 转换为 Visio (.vsdx) 格式。
.vsdx 本质是 ZIP 包，内含多个 XML 文件，符合 Open Packaging Convention。
"""

import xml.etree.ElementTree as ET
import zipfile
import re
import html as html_module
import sys
from pathlib import Path


def strip_html(text: str) -> str:
    """去除 HTML 标签，把 <br> 换成换行，解码 HTML 实体。"""
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


def build_content_types(page_count: int) -> str:
    page_overrides = '\n'.join(
        f'  <Override PartName="/visio/pages/page{i}.xml" '
        f'ContentType="application/vnd.ms-visio.page+xml"/>'
        for i in range(1, page_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>
  <Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
{page_overrides}
</Types>'''


ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>'''


APP_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Application>Microsoft Visio</Application>
  <AppVersion>16.0000</AppVersion>
</Properties>'''


CORE_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
  xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>draw.io Converter</dc:creator>
  <cp:revision>1</cp:revision>
</cp:coreProperties>'''


def build_document_xml() -> str:
    return '''<?xml version="1.0" encoding="utf-8"?>
<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main"
               xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
               xml:space="preserve">
  <DocumentProperties>
    <Creator>draw.io Converter</Creator>
    <Title>Converted from draw.io</Title>
  </DocumentProperties>
  <DocumentSheet NameU="TheDoc" LineStyle="0" FillStyle="0" TextStyle="0"/>
  <Masters/>
  <Pages r:id="rId1"/>
</VisioDocument>'''


DOCUMENT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>
</Relationships>'''


def build_pages_xml(pages_data: list) -> str:
    """pages_data: [(name, page_w_in, page_h_in), ...]"""
    page_items = []
    for i, (name, pw_in, ph_in) in enumerate(pages_data, start=1):
        page_items.append(
            f'  <Page ID="{i-1}" NameU="{xml_escape(name)}" Name="{xml_escape(name)}">\n'
            f'    <PageSheet LineStyle="0" FillStyle="0" TextStyle="0">\n'
            f'      <PageProps>\n'
            f'        <PageWidth>{pw_in:.5f}</PageWidth>\n'
            f'        <PageHeight>{ph_in:.5f}</PageHeight>\n'
            f'        <PageScale>1</PageScale>\n'
            f'        <DrawingScale>1</DrawingScale>\n'
            f'      </PageProps>\n'
            f'    </PageSheet>\n'
            f'    <Rel r:id="rId{i}"/>\n'
            f'  </Page>'
        )
    page_block = '\n'.join(page_items)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xml:space="preserve">
{page_block}
</Pages>'''


def build_pages_rels(page_count: int) -> str:
    rels = '\n'.join(
        f'  <Relationship Id="rId{i}" '
        f'Type="http://schemas.microsoft.com/visio/2010/relationships/page" '
        f'Target="page{i}.xml"/>'
        for i in range(1, page_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{rels}
</Relationships>'''


def build_page_xml(shapes: list, page_h_in: float, dpi: float = 96.0) -> str:
    """把 draw.io 形状列表转成 Visio PageContents XML。"""
    shape_xmls = []
    for i, s in enumerate(shapes, start=1):
        # draw.io: 原点左上，单位 px
        # Visio:   原点左下，单位 英寸
        # PinX/PinY 是形状中心点坐标
        pin_x = (s['x'] + s['w'] / 2) / dpi
        pin_y = page_h_in - (s['y'] + s['h'] / 2) / dpi
        width = s['w'] / dpi
        height = s['h'] / dpi
        label = xml_escape(s['label'])

        shape_xmls.append(f'''  <Shape ID="{i}" Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">
    <XForm>
      <PinX>{pin_x:.5f}</PinX>
      <PinY>{pin_y:.5f}</PinY>
      <Width>{width:.5f}</Width>
      <Height>{height:.5f}</Height>
      <LocPinX F="Width*0.5">{width/2:.5f}</LocPinX>
      <LocPinY F="Height*0.5">{height/2:.5f}</LocPinY>
    </XForm>
    <Geom IX="0">
      <MoveTo IX="1"><X F="Width*0">0</X><Y F="Height*0">0</Y></MoveTo>
      <LineTo IX="2"><X F="Width*1">{width:.5f}</X><Y F="Height*0">0</Y></LineTo>
      <LineTo IX="3"><X F="Width*1">{width:.5f}</X><Y F="Height*1">{height:.5f}</Y></LineTo>
      <LineTo IX="4"><X F="Width*0">0</X><Y F="Height*1">{height:.5f}</Y></LineTo>
      <LineTo IX="5"><X F="Width*0">0</X><Y F="Height*0">0</Y></LineTo>
    </Geom>
    <Text>{label}</Text>
  </Shape>''')

    shapes_block = '\n'.join(shape_xmls)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
              xml:space="preserve">
  <Shapes>
{shapes_block}
  </Shapes>
  <Connects/>
</PageContents>'''


PAGE_RELS_EMPTY = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>'''


def parse_drawio(drawio_path: str):
    """解析 .drawio 文件，返回 [(page_name, page_w, page_h, shapes), ...]。"""
    tree = ET.parse(drawio_path)
    root = tree.getroot()
    pages = []

    for diagram in root.findall('diagram'):
        name = diagram.get('name', 'Page')
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
            if cid in ('0', '1'):
                continue
            if cell.get('vertex') != '1':
                continue

            geo = cell.find('mxGeometry')
            if geo is None:
                continue

            x = float(geo.get('x', 0))
            y = float(geo.get('y', 0))
            w = float(geo.get('width', 100))
            h = float(geo.get('height', 40))
            label = strip_html(cell.get('value', ''))

            shapes.append({'x': x, 'y': y, 'w': w, 'h': h, 'label': label})

        pages.append((name, page_w, page_h, shapes))

    return pages


def convert(drawio_path: str, vsdx_path: str):
    pages = parse_drawio(drawio_path)
    if not pages:
        print('未找到任何图页，转换终止。')
        return

    DPI = 96.0
    # 所有页面使用第一页的尺寸（draw.io 多页通常尺寸相同）
    _, page_w_px, page_h_px, _ = pages[0]
    page_w_in = page_w_px / DPI
    page_h_in = page_h_px / DPI

    pages_meta = [(p[0], p[1] / DPI, p[2] / DPI) for p in pages]

    with zipfile.ZipFile(vsdx_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', build_content_types(len(pages)))
        zf.writestr('_rels/.rels', ROOT_RELS)
        zf.writestr('docProps/app.xml', APP_XML)
        zf.writestr('docProps/core.xml', CORE_XML)
        zf.writestr('visio/document.xml', build_document_xml())
        zf.writestr('visio/_rels/document.xml.rels', DOCUMENT_RELS)
        zf.writestr('visio/pages/pages.xml', build_pages_xml(pages_meta))
        zf.writestr('visio/pages/_rels/pages.xml.rels', build_pages_rels(len(pages)))

        for i, (pname, pw, ph, shapes) in enumerate(pages, start=1):
            ph_in = ph / DPI
            page_content = build_page_xml(shapes, ph_in, DPI)
            zf.writestr(f'visio/pages/page{i}.xml', page_content)
            zf.writestr(f'visio/pages/_rels/page{i}.xml.rels', PAGE_RELS_EMPTY)

    total_shapes = sum(len(p[3]) for p in pages)
    print(f'转换完成：{len(pages)} 个页面，共 {total_shapes} 个形状')
    print(f'输出文件：{vsdx_path}')


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else '/home/yyaction/航空安保一体化平台拓扑图.drawio'
    dst = sys.argv[2] if len(sys.argv) > 2 else str(Path(src).with_suffix('.vsdx'))
    convert(src, dst)
