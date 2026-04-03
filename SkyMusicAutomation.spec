# -*- mode: python ; coding: utf-8 -*-
import sys

a = Analysis(
    ['src/interfaces/gui/app.py'],
    pathex=[],
    binaries=[],
    datas=[('configs', 'configs'), ('assets/icon.png', 'assets')],
    hiddenimports=[
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        'pynput.keyboard._xorg',
        'pynput.mouse._xorg',
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
        'PySide6.QtBluetooth', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtNfc', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtPositioning',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets', 'PySide6.QtRemoteObjects',
        'PySide6.QtScxml', 'PySide6.QtSensors',
        'PySide6.QtSerialBus', 'PySide6.QtSerialPort',
        'PySide6.QtSpatialAudio', 'PySide6.QtSql',
        'PySide6.QtStateMachine', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
        'PySide6.QtTest', 'PySide6.QtWebChannel',
        'PySide6.QtWebEngine', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebSockets', 'PySide6.QtXml',
        'PySide6.QtNetwork',
    ],
    noarchive=False,
    optimize=0,
)

# Strip unnecessary Qt DLLs and data that the exclude list doesn't catch
_UNWANTED_BINS = {
    'opengl32sw.dll',
    'Qt6Quick.dll', 'Qt6Qml.dll', 'Qt6QmlModels.dll', 'Qt6QmlMeta.dll',
    'Qt6Pdf.dll', 'Qt6OpenGL.dll', 'Qt6Network.dll',
    'Qt6Svg.dll', 'Qt6VirtualKeyboard.dll',
    'QtNetwork.pyd',
}
a.binaries = [b for b in a.binaries if b[0].split('/')[-1].split('\\')[-1] not in _UNWANTED_BINS]

# Strip Qt translations and unnecessary plugins
a.datas = [d for d in a.datas if not d[0].startswith(('PySide6/translations',
                                                       'PySide6\\translations'))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SkyMusicAutomation',
    icon='assets/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SkyMusicAutomation',
)
