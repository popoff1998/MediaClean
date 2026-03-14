# MediaClean 🎬

**Organizador de series descargadas para Plex** — Aplicación de escritorio (Python + Qt) que escanea tus carpetas de series descargadas por torrent, identifica los episodios usando **TVDB** (TheTVDB) u **OMDB** (Open Movie Database) y crea una carpeta de salida con los vídeos renombrados al formato que Plex reconoce automáticamente.

Cuando se usa OMDB (que solo devuelve títulos en inglés), MediaClean consulta automáticamente **Wikidata** para obtener los nombres de series y episodios en **castellano**.

## Características

- **Escaneo inteligente**: Detecta archivos de vídeo (`.mkv`, `.avi`, `.mp4`, `.mov`, etc.) dentro de estructuras de carpetas complejas e intenta deducir la serie desde carpetas y ficheros internos, no solo desde la carpeta contenedora.
- **Carpetas multi-temporada**: Puede procesar un lote que incluya varias temporadas de la misma serie y enviar cada episodio a su `Season XX` correcta.
- **Detección de episodios**: Extrae temporada y número de episodio de nombres de archivo con patrones como `S01E01`, `1x01`, `Capitulo 01`, etc.
- **Integración TVDB / OMDB**: Busca la serie en TheTVDB o en Open Movie Database y obtiene los títulos oficiales de cada episodio.
- **Traducción vía Wikidata**: Cuando se usa OMDB (solo inglés), los títulos se traducen automáticamente al castellano consultando Wikidata (SPARQL, propiedad `P345`).
- **Renombrado Plex**: Genera nombres compatibles con Plex: `Serie - S01E01 - Título del Episodio.mkv`
- **Mover o copiar**: Por defecto mueve los archivos a una carpeta de salida limpia, pero puedes cambiar la operación a copia si prefieres conservar los originales en su ubicación inicial.
- **Hard links opcionales**: Ahorra espacio en disco creando hard links en lugar de copias (mismo disco necesario).
- **Interfaz moderna**: GUI con tema oscuro estilo Catppuccin.

## Requisitos

- Python 3.10+
- PySide6
- Una API Key de **TVDB** o de [OMDB](https://www.omdbapi.com/apikey.aspx)

## Instalación

```bash
# Clonar el repositorio
git clone https://github.com/tu-usuario/MediaClean.git
cd MediaClean

# Crear entorno virtual (recomendado)
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # Linux/macOS

# Instalar dependencias
pip install -r requirements.txt
```

## Uso

```bash
python main.py
```

### Flujo de trabajo

1. **Seleccionar carpeta**: Pulsa "Explorar…" y selecciona la carpeta raíz de la serie descargada.
2. **Escanear**: Pulsa "Escanear carpeta" para detectar los archivos de vídeo.
3. **Configurar API**: Introduce tu API Key (TVDB u OMDB) y busca el nombre de la serie.
4. **Seleccionar**: Haz clic en la serie correcta de la lista de resultados.
5. **Previsualizar**: Revisa en la tabla cómo se renombrarán los archivos.
6. **Ejecutar**: Pulsa "Ejecutar" para crear la carpeta de salida con los vídeos renombrados.

### Estructura de salida

```text
_MediaClean_Output/
  Nombre de la Serie/
    Season 01/
      Nombre de la Serie - S01E01 - Título del Episodio.mkv
      Nombre de la Serie - S01E02 - Título del Episodio.avi
    Season 02/
      ...
```

Esta carpeta se puede mover directamente a tu biblioteca de Plex.

## Obtener API Key

### TVDB

1. Usa tu API Key de TheTVDB en el selector de proveedor.
2. Si tu cuenta o plan requiere PIN, introdúcelo también en el campo **PIN TVDB**.
3. MediaClean usará esa clave para buscar la serie y cargar temporadas y episodios.

### OMDB

1. Solicita una clave gratuita en [omdbapi.com/apikey.aspx](https://www.omdbapi.com/apikey.aspx)
2. Recibirás la clave por correo electrónico
3. OMDB devuelve los datos en inglés; MediaClean traduce automáticamente al castellano usando **Wikidata** (sin necesidad de clave adicional)

## Estructura del proyecto

```text
MediaClean/
├── main.py                    # Punto de entrada
├── requirements.txt           # Dependencias
├── README.md
└── mediaclean/
    ├── __init__.py
    ├── __main__.py
    ├── constants.py           # Constantes y patrones
    ├── scanner.py             # Escaneo de carpetas y detección de episodios
    ├── tvdb_client.py         # Cliente API de TVDB
    ├── omdb_client.py         # Cliente API de OMDB
    ├── wikidata_client.py     # Traducción de títulos vía Wikidata SPARQL
    ├── renamer.py             # Lógica de renombrado y copia
    └── ui/
        ├── __init__.py
        ├── style.py           # Estilos Qt (tema oscuro)
        ├── workers.py         # Hilos de trabajo (QThread)
        └── main_window.py     # Ventana principal
```

## Licencia

MIT
