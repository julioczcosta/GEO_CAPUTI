import math
import os
import time
from qgis.core import (
    QgsProject, QgsLayoutItemMap, QgsLayoutItemScaleBar, QgsLayoutPoint,
    QgsUnitTypes, QgsLayoutItem, QgsRasterLayer, QgsLayoutExporter, Qgis,
    QgsLayoutItemLegend, QgsLayoutItemPage, QgsLayoutItemGroup
)
from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtGui import QImage, QPainter
from qgis.utils import iface
# ==============================================================================
# --- 1. CONFIGURAÇÕES DA EXPORTAÇÃO ---
# ==============================================================================
PASTA_DESTINO = r"C:\Users\juliocaputi\OneDrive - caputiavaliacoes.com.br\Arquivos de Marina Diotto - GEO\Teste_Automatiza\Output"
# Estilos
PATH_ESTILO_GERAL      = r"C:\Users\juliocaputi\OneDrive - caputiavaliacoes.com.br\Arquivos de Marina Diotto - GEO\01GEOCAPUTI\11Estilo\estilo_perimetro_mat.qml"
PATH_ESTILO_INDIVIDUAL = r"C:\Users\juliocaputi\OneDrive - caputiavaliacoes.com.br\Arquivos de Marina Diotto - GEO\01GEOCAPUTI\11Estilo\estilo_padrao_linha.qml"
NOME_CAMADA_ALVO   = 'Perímetro da propriedade'
NOME_LAYOUT        = 'Perimetro'
CAMPO_MATRICULA    = 'Matricula'
NOME_CAMADA_GOOGLE = 'Google Satellite' # <--- Nome do mapa base para usar de fallback
# --- CONTROLE DE ZOOM ---
ZOOM_GERAL = 1.1      # Zoom do mapa com TODAS as matrículas
ZOOM_INDIVIDUAL = 1.4 # Zoom para cada matrícula isolada
# ---> AJUSTE VISUAL DO PERÍMETRO NO QUADRO <---
DESLOCAMENTO_Y_MAPA = 0.00
# ---> POSICIONAMENTO DA ESCALA E DO NORTE <---
# O script RENDERIZA a escala e o Norte sozinhos numa imagem pequena e MEDE
# em pixels o que aparece desenhado (barra, rótulos e seta). Com isso:
#   - o MEIO DA BARRA e a SETA do Norte ficam no centro X da 'grade', no mesmo
#     eixo do texto centralizado da caixa;
#   - a barra fica com o MAIOR valor redondo que caiba, contando só o que é
#     desenhado ("0" ... "1,6 km"), sem as margens invisíveis da caixa do item.
ID_GRADE   = 'grade'
# Fração da largura da 'grade' que o desenho da escala pode ocupar, medida a
# partir do meio da barra (o lado do "km" é o que encosta primeiro).
# 0.98 = quase encostando na borda. Diminua se quiser mais folga.
FATOR_LARGURA_GRADE = 0.98
# Ajuste fino do Norte no eixo X, em mm. Deve ficar 0 agora que é medido.
# NEGATIVO = move o Norte para a ESQUERDA | POSITIVO = DIREITA.
AJUSTE_NORTE_X = 0.0
ESPACO_NORTE   = 0.0   # mm entre o topo da escala e a base do Norte
DPI_MEDICAO    = 300   # resolução da renderização usada só para medir
ID_ESCALA  = 'Escala'
ID_NORTE   = 'Norte'
ID_LEGENDA = 'Legenda'
OPCOES_ARREDONDAMENTO = [1, 1.25, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7.5, 8]
LIMITE_PARA_KM = 500
# ==============================================================================
# --- 2. FUNÇÕES AUXILIARES ---
# ==============================================================================
def encontrar_camada(nome_procurado, project):
    for layer in project.mapLayers().values():
        if layer.name().strip().lower() == nome_procurado.strip().lower():
            return layer
    return None
def buscar_tiffs_satelite(project):
    """ Encontra APENAS imagens locais (TIFF), ignorando Basemaps web """
    tiffs_fundo = []
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsRasterLayer):
            if layer.providerType() == 'gdal':
                if "declividade" not in layer.name().lower():
                    tiffs_fundo.append(layer)
    return tiffs_fundo
def aplicar_zoom_deslocado(mapa, rect, zoom, deslocamento_y):
    """ Dá o zoom e move a 'câmera' para corrigir o centro visual do polígono """
    rect.scale(zoom)
    dy = rect.height() * deslocamento_y
    rect.setYMinimum(rect.yMinimum() - dy)
    rect.setYMaximum(rect.yMaximum() - dy)
    mapa.zoomToExtent(rect)
def gerar_candidatos(valor_maximo):
    """ Lista de valores 'redondos' (opções x potências de 10), do MAIOR para o menor """
    candidatos = []
    if valor_maximo <= 0:
        return candidatos
    pot_max = math.floor(math.log10(valor_maximo))
    for expoente in range(pot_max, pot_max - 5, -1):   # 5 décadas abaixo já é folga suficiente
        pot = 10.0 ** expoente
        for opt in sorted(OPCOES_ARREDONDAMENTO, reverse=True):
            valor = opt * pot
            if valor <= valor_maximo:
                candidatos.append(float(valor))
    return candidatos
def aplicar_segmento(escala, metros_por_segmento):
    """ Aplica o valor do segmento (em metros) escolhendo a unidade (m ou km)
        e força o item a recalcular sua própria caixa """
    if metros_por_segmento >= LIMITE_PARA_KM:
        escala.setUnits(QgsUnitTypes.DistanceUnit.DistanceKilometers)
        escala.setUnitsPerSegment(metros_por_segmento / 1000.0)
        escala.setUnitLabel("km")
    else:
        escala.setUnits(QgsUnitTypes.DistanceUnit.DistanceMeters)
        escala.setUnitsPerSegment(metros_por_segmento)
        escala.setUnitLabel("m")
    escala.update()
    try:
        escala.refresh()          # garante que rect() já reflete o novo tamanho
    except Exception:
        pass
    return escala.rect().width()   # largura REAL do item (barra + rótulos), em mm
def _renderizar_sozinho(layout, item):
    """ Renderiza SÓ este item (sem moldura/fundo) sobre fundo branco.
        Retorna (imagem em tons de cinza, pixels por mm, x esquerdo da região em mm) """
    ocultos = []
    for it in layout.items():
        if it is item or not isinstance(it, QgsLayoutItem):
            continue
        if isinstance(it, (QgsLayoutItemPage, QgsLayoutItemGroup)):
            continue
        if it.isVisible():
            it.setVisibility(False)
            ocultos.append(it)
    moldura, fundo = item.frameEnabled(), item.hasBackground()
    item.setFrameEnabled(False)
    item.setBackgroundEnabled(False)
    try:
        regiao = item.sceneBoundingRect()
        img = QgsLayoutExporter(layout).renderRegionToImage(regiao, QSize(), DPI_MEDICAO)
    finally:
        item.setFrameEnabled(moldura)
        item.setBackgroundEnabled(fundo)
        for it in ocultos:
            it.setVisibility(True)
    base = QImage(img.size(), QImage.Format_RGB32)
    base.fill(Qt.white)
    p = QPainter(base)
    p.drawImage(0, 0, img)
    p.end()
    cinza = base.convertToFormat(QImage.Format_Grayscale8)
    return cinza, img.width() / regiao.width(), regiao.left()
def _linhas(cinza):
    """ Devolve, linha a linha, os bytes de cinza da imagem (0 = preto, 255 = branco) """
    w, h, bpl = cinza.width(), cinza.height(), cinza.bytesPerLine()
    ptr = cinza.constBits()
    ptr.setsize(cinza.sizeInBytes() if hasattr(cinza, 'sizeInBytes') else cinza.byteCount())
    dados = bytes(ptr)
    for y in range(h):
        yield dados[y * bpl: y * bpl + w]
def medir_escala(layout, escala):
    """ Mede o que a escala DESENHA de fato (mm, coordenadas do layout):
        - barra: a linha com mais pixels pretos é o contorno da barra zebrada
          (os textos têm bem menos pixels por linha)
        - tinta: tudo que aparece ("0" ... "1,6 km"), sem as margens invisíveis
          que a caixa do item tem
        Retorna (barra_esq, barra_dir, tinta_esq, tinta_dir) ou None """
    cinza, ppm, x0 = _renderizar_sozinho(layout, escala)
    barra, tmin, tmax = [], None, None
    for linha in _linhas(cinza):
        tinta = [x for x, v in enumerate(linha) if v < 200]
        if not tinta:
            continue
        tmin = tinta[0] if tmin is None else min(tmin, tinta[0])
        tmax = tinta[-1] if tmax is None else max(tmax, tinta[-1])
        preto = [x for x in tinta if linha[x] < 100]
        if len(preto) > len(barra):
            barra = preto
    if not barra:
        return None
    return (x0 + barra[0] / ppm, x0 + (barra[-1] + 1) / ppm,
            x0 + tmin / ppm, x0 + (tmax + 1) / ppm)
def medir_centro_norte(layout, norte):
    """ Centro X REAL do desenho da seta (ignora espaço vazio do SVG) """
    cinza, ppm, x0 = _renderizar_sozinho(layout, norte)
    xmin, xmax = None, None
    for linha in _linhas(cinza):
        xs = [x for x, v in enumerate(linha) if v < 200]
        if xs:
            xmin = xs[0] if xmin is None else min(xmin, xs[0])
            xmax = xs[-1] if xmax is None else max(xmax, xs[-1])
    if xmin is None:
        return norte.sceneBoundingRect().center().x()
    return x0 + (xmin + xmax + 1) / 2.0 / ppm
def ajustar_escala_e_posicao(mapa, escala, norte, grade):
    """ Escolhe o maior valor redondo que caiba na grade, crava o MEIO DA BARRA
        no centro da grade e o Norte no mesmo eixo (tudo medido em pixels) """
    layout = escala.layout()
    rect_grade = grade.sceneBoundingRect()
    cx = rect_grade.center().x()
    y_base = rect_grade.bottom()
    largura_grade = rect_grade.width()
    meia_util = largura_grade * FATOR_LARGURA_GRADE / 2.0
    scale_val = mapa.scale()
    if scale_val <= 0 or meia_util <= 0:
        return

    try:
        num_segs = escala.numberOfSegments()
        if hasattr(escala, 'numberOfSegmentsLeft'):
            num_segs += escala.numberOfSegmentsLeft()
    except Exception:
        num_segs = 2
    num_segs = max(num_segs, 1)

    # A barra sozinha nunca passa da largura útil: isso limita o maior candidato
    # (5% de folga porque é só estimativa; quem decide é a medição)
    max_por_segmento = (2.0 * meia_util / 1000.0) * scale_val / num_segs
    candidatos = gerar_candidatos(max_por_segmento * 1.05)

    # Do maior para o menor: fica no primeiro cujo desenho, a partir do meio da
    # barra, caiba para os DOIS lados (o lado do "km" é o maior)
    escolhido, medida = None, None
    for valor in candidatos:
        aplicar_segmento(escala, valor)
        m = medir_escala(layout, escala)
        if not m:
            continue
        c = (m[0] + m[1]) / 2.0
        if max(c - m[2], m[3] - c) <= meia_util:
            escolhido, medida = valor, m
            break
    if escolhido is None:
        escolhido = candidatos[-1] if candidatos else max_por_segmento
        aplicar_segmento(escala, escolhido)
        medida = medir_escala(layout, escala)

    # Meio da barra no centro da grade, escala apoiada na base da grade
    if medida:
        c_rel = (medida[0] + medida[1]) / 2.0 - escala.pos().x()
    else:
        c_rel = escala.rect().width() / 2.0
    escala.setReferencePoint(QgsLayoutItem.ReferencePoint.LowerLeft)
    escala.attemptMove(QgsLayoutPoint(cx - c_rel, y_base, QgsUnitTypes.LayoutUnit.LayoutMillimeters))

    # Mede de novo já no lugar: é no meio REAL da barra que o Norte crava
    medida = medir_escala(layout, escala)
    cx_barra = (medida[0] + medida[1]) / 2.0 if medida else cx

    # Centro do DESENHO do Norte no eixo da barra, logo acima da escala
    if norte:
        n_rel = medir_centro_norte(layout, norte) - norte.pos().x()
        norte.setReferencePoint(QgsLayoutItem.ReferencePoint.LowerLeft)
        norte.attemptMove(QgsLayoutPoint(cx_barra - n_rel + AJUSTE_NORTE_X,
                                         escala.pos().y() - ESPACO_NORTE,
                                         QgsUnitTypes.LayoutUnit.LayoutMillimeters))

    # Diagnóstico: os dois desvios devem ficar perto de +0.00
    desvio_norte = (medir_centro_norte(layout, norte) - cx_barra) if norte else 0.0
    if medida:
        folga_esq = medida[2] - rect_grade.left()
        folga_dir = rect_grade.right() - medida[3]
    else:
        folga_esq = folga_dir = float('nan')
    print(f"    [escala] segmento {escolhido:g} m | grade {largura_grade:.1f} mm | "
          f"folga esq {folga_esq:.1f} mm / dir {folga_dir:.1f} mm | "
          f"barra-grade={cx_barra - cx:+.2f} mm | norte-barra={desvio_norte:+.2f} mm")
# ==============================================================================
# --- 3. EXECUÇÃO DA EXPORTAÇÃO ---
# ==============================================================================
def main():
    if not os.path.exists(PASTA_DESTINO):
        os.makedirs(PASTA_DESTINO)
    if iface:
        iface.messageBar().pushMessage("Exportação", "Iniciando exportações (Geral + Individuais)...", level=Qgis.Info, duration=4)

    project = QgsProject.instance()

    layer_alvo = encontrar_camada(NOME_CAMADA_ALVO, project)
    if not layer_alvo:
        return print(f"ERRO: Camada '{NOME_CAMADA_ALVO}' não encontrada.")
    manager = project.layoutManager()
    layout = manager.layoutByName(NOME_LAYOUT)
    if not layout: return print(f"ERRO: Layout '{NOME_LAYOUT}' não encontrado.")
    escala = layout.itemById(ID_ESCALA)
    norte = layout.itemById(ID_NORTE)
    grade = layout.itemById(ID_GRADE)
    if not escala: return print(f"ERRO: Item '{ID_ESCALA}' não encontrado no layout.")
    if not grade: return print(f"ERRO: Item '{ID_GRADE}' não encontrado no layout.")
    mapa = escala.linkedMap()
    if not mapa:
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap): mapa = item; break
    if not mapa: return print("ERRO: Mapa não encontrado no layout.")
    legenda = layout.itemById(ID_LEGENDA)
    if not legenda:
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLegend):
                legenda = item; break

    node_perimetro = None
    if legenda:
        legenda.setAutoUpdateModel(False)
        root_legenda = legenda.model().rootGroup()
        node_perimetro = root_legenda.findLayer(layer_alvo)
        if not node_perimetro:
            node_perimetro = root_legenda.addLayer(layer_alvo)
    exporter = QgsLayoutExporter(layout)
    image_settings = QgsLayoutExporter.ImageExportSettings()
    image_settings.dpi = 300
    tiffs_dinamicos = buscar_tiffs_satelite(project)

    if not tiffs_dinamicos:
        camada_google = encontrar_camada(NOME_CAMADA_GOOGLE, project)
        if camada_google:
            tiffs_dinamicos = [camada_google]
            print(f"-> TIFF não encontrado. Usando '{NOME_CAMADA_GOOGLE}' como fallback.")
        else:
            print("-> AVISO: Nenhum TIFF e nem o 'Google Satellite' foram encontrados.")
    mapa.setFollowVisibilityPreset(False)
    mapa.setLayers([layer_alvo] + tiffs_dinamicos)
    mapa.setKeepLayerSet(True)
    nome_limpo_alvo = NOME_CAMADA_ALVO.strip()
    # ==============================================================================
    # 1º PASSO: EXPORTAR O MAPA GERAL
    # ==============================================================================
    print("\n>>> 1. Gerando Mapa Geral (Todas as Matrículas)...")

    if os.path.exists(PATH_ESTILO_GERAL):
        layer_alvo.loadNamedStyle(PATH_ESTILO_GERAL)
        layer_alvo.triggerRepaint()
    layer_alvo.setSubsetString("")

    if node_perimetro and legenda:
        node_perimetro.setName(nome_limpo_alvo)
        legenda.refresh()
        legenda.adjustBoxSize()

    layout.refresh()
    time.sleep(0.5)
    mapa.setCrs(layer_alvo.crs())
    rect_geral = layer_alvo.extent()
    aplicar_zoom_deslocado(mapa, rect_geral, ZOOM_GERAL, DESLOCAMENTO_Y_MAPA)

    ajustar_escala_e_posicao(mapa, escala, norte, grade)
    mapa.storeCurrentLayerStyles()
    mapa.setKeepLayerStyles(True)

    layout.refresh()
    time.sleep(1.0)
    caminho_geral = os.path.join(PASTA_DESTINO, "Perimetro.png")
    exporter.exportToImage(caminho_geral, image_settings)
    # ==============================================================================
    # 2º PASSO: EXPORTAR OS MAPAS INDIVIDUAIS
    # ==============================================================================
    print("\n>>> 2. Gerando Mapas Individuais...")
    if os.path.exists(PATH_ESTILO_INDIVIDUAL):
        layer_alvo.loadNamedStyle(PATH_ESTILO_INDIVIDUAL)
        layer_alvo.triggerRepaint()
    feicoes = list(layer_alvo.getFeatures())
    total = len(feicoes)
    for i, feat in enumerate(feicoes):
        valor_mat = feat.attribute(CAMPO_MATRICULA)
        if not valor_mat: valor_mat = f"ID_{feat.id()}"

        nome_arquivo = f"Perimetro_{str(valor_mat).replace('/', '-').replace(':', '')}.png"
        caminho_png = os.path.join(PASTA_DESTINO, nome_arquivo)
        print(f" -> Processando {i+1}/{total}: {nome_arquivo}")
        if isinstance(valor_mat, (int, float)):
            filtro = f'"{CAMPO_MATRICULA}" = {valor_mat}'
        else:
            filtro = f'"{CAMPO_MATRICULA}" = \'{valor_mat}\''
        layer_alvo.setSubsetString(filtro)
        time.sleep(0.5)
        geom = feat.geometry()
        if geom.isNull(): continue
        rect_feat = geom.boundingBox()
        aplicar_zoom_deslocado(mapa, rect_feat, ZOOM_INDIVIDUAL, DESLOCAMENTO_Y_MAPA)
        ajustar_escala_e_posicao(mapa, escala, norte, grade)
        if node_perimetro and legenda:
            node_perimetro.setName(f"{nome_limpo_alvo} ({valor_mat})")
            legenda.refresh()
            legenda.adjustBoxSize()
        mapa.storeCurrentLayerStyles()
        mapa.setKeepLayerStyles(True)
        layout.refresh()
        time.sleep(1.0)
        exporter.exportToImage(caminho_png, image_settings)
    # ==============================================================================
    # 3º PASSO: LIMPEZA E RESTAURAÇÃO
    # ==============================================================================
    print("\n>>> Exportação concluída. Limpando filtros e restaurando legenda...")

    layer_alvo.setSubsetString("")

    if node_perimetro and legenda:
        node_perimetro.setName(nome_limpo_alvo)
        legenda.refresh()
        legenda.adjustBoxSize()
    layout.refresh()
    time.sleep(0.5)

    layer_alvo.triggerRepaint()
    if iface:
        iface.mapCanvas().refresh()
        iface.messageBar().pushMessage("Sucesso", f"Mapa Geral e {total} Mapas Individuais gerados na pasta Output!", level=Qgis.Success, duration=8)
main()
