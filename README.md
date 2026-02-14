# MediaClean 🎬

**Organizador de series descargadas para Plex** — Aplicación de escritorio (Python + Qt) que escanea tus carpetas de series descargadas por torrent, identifica los episodios usando **TMDB** (The Movie Database) y crea una carpeta de salida con los vídeos renombrados al formato que Plex reconoce automáticamente.

## Características

- **Escaneo inteligente**: Detecta archivos de vídeo (`.mkv`, `.avi`, `.mp4`, `.mov`, etc.) dentro de estructuras de carpetas complejas.
- **Detección de episodios**: Extrae temporada y número de episodio de nombres de archivo con patrones como `S01E01`, `1x01`, `Capitulo 01`, etc.
- **Integración TMDB**: Busca la serie en The Movie Database y obtiene los títulos oficiales de cada episodio.
- **Renombrado Plex**: Genera nombres compatibles con Plex: `Serie - S01E01 - Título del Episodio.mkv`
- **No destructivo**: Los archivos originales **nunca se modifican ni se borran**. Se copian (o enlazan) a una carpeta de salida independiente.
- **Hard links opcionales**: Ahorra espacio en disco creando hard links en lugar de copias (mismo disco necesario).
- **Interfaz moderna**: GUI con tema oscuro estilo Catppuccin.

## Requisitos

- Python 3.10+
- PySide6
- Una API Key gratuita de [TMDB](https://www.themoviedb.org/settings/api)

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
3. **Configurar TMDB**: Introduce tu API Key y busca el nombre de la serie.
4. **Seleccionar**: Haz clic en la serie correcta de la lista de resultados.
5. **Previsualizar**: Revisa en la tabla cómo se renombrarán los archivos.
6. **Ejecutar**: Pulsa "Ejecutar" para crear la carpeta de salida con los vídeos renombrados.

### Estructura de salida

```
_MediaClean_Output/
  Nombre de la Serie/
    Season 01/
      Nombre de la Serie - S01E01 - Título del Episodio.mkv
      Nombre de la Serie - S01E02 - Título del Episodio.avi
    Season 02/
      ...
```

Esta carpeta se puede mover directamente a tu biblioteca de Plex.

## Obtener API Key de TMDB

1. Crea una cuenta en [themoviedb.org](https://www.themoviedb.org/signup)
2. Ve a [Configuración > API](https://www.themoviedb.org/settings/api)
3. Solicita una API Key (selecciona "Developer")
4. Copia la clave "API Key (v3 auth)" y pégala en MediaClean

## Estructura del proyecto

```
MediaClean/
├── main.py                    # Punto de entrada
├── requirements.txt           # Dependencias
├── README.md
└── mediaclean/
    ├── __init__.py
    ├── __main__.py
    ├── constants.py           # Constantes y patrones
    ├── scanner.py             # Escaneo de carpetas y detección de episodios
    ├── tmdb_client.py         # Cliente API de TMDB
    ├── renamer.py             # Lógica de renombrado y copia
    └── ui/
        ├── __init__.py
        ├── style.py           # Estilos Qt (tema oscuro)
        ├── workers.py         # Hilos de trabajo (QThread)
        └── main_window.py     # Ventana principal
```

## Licencia

MIT
