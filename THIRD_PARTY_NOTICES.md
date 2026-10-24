# Сторонние компоненты

PLDL распространяется под GNU General Public License v3.0
(`GPL-3.0-only`). Ниже перечислены основные сторонние компоненты,
которые используются приложением или входят в собранный exe. Каждый
компонент остаётся под своей лицензией; PLDL не меняет и не перелицензирует
его условия.

## Основные зависимости

| Компонент | Лицензия | Примечание |
|---|---|---|
| [Python](https://www.python.org/) | PSF-2.0 | среда выполнения, встроена в one-file exe |
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0-or-later с исключением для bootloader | bootloader входит в собранный exe; исключение разрешает распространение приложений под другими лицензиями |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | локальный HTTP API |
| [Werkzeug](https://werkzeug.palletsprojects.com/) | BSD-3-Clause | HTTP-сервер и утилиты |
| [Jinja2](https://jinja.palletsprojects.com/) | BSD-3-Clause | шаблоны |
| [requests](https://requests.readthedocs.io/) | Apache-2.0 | HTTP-запросы к сервисам |
| [urllib3](https://urllib3.readthedocs.io/) | MIT | транспорт для requests |
| [certifi](https://certifi.io/) | MPL-2.0 | набор корневых сертификатов |
| [charset-normalizer](https://github.com/Ousret/charset_normalizer) | MIT | определение кодировки |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause | Internationalized Domain Names |
| [mutagen](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later | запись ID3/MP4-тегов и обложек |
| [pywebview](https://pywebview.flowrl.com/) | BSD-3-Clause | нативное окно WebView2 |
| [pythonnet](https://pythonnet.github.io/) | MIT | интеграция с .NET для pywebview |
| [clr-loader](https://github.com/pythonnet/clr-loader) | MIT | загрузка .NET runtime |
| [bottle](https://bottlepy.org/) | MIT | мини-сервер pywebview |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | загрузка и поиск аудио |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | Unlicense AND MIT AND ISC | JavaScript-решатель для yt-dlp |
| [brotli](https://github.com/google/brotli) | MIT | сжатие HTTP-ответов |
| [pycryptodomex](https://www.pycryptodome.org/) | BSD-2-Clause / Public Domain | криптография в зависимостях yt-dlp |
| [websockets](https://websockets.readthedocs.io/) | BSD-3-Clause | WebSocket-транспорт |

Часть перечисленных пакетов имеет транзитивные зависимости. Их лицензии
указаны в метаданных соответствующих пакетов и на PyPI.

## Инструменты и внешние компоненты

Эти компоненты не входят в поставку PLDL и устанавливаются отдельно:

| Компонент | Лицензия | Примечание |
|---|---|---|
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Inno Setup License | только сборка установщика |
| [FFmpeg](https://ffmpeg.org/) | LGPL-2.1-or-later или GPL-2.0-or-later в зависимости от сборки | внешний конвертер; пользователь устанавливает его самостоятельно |
| [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2/) | условия Microsoft | системный компонент Windows для окна приложения |

## Совместимость лицензий

`mutagen` распространяется по `GPL-2.0-or-later`. PLDL использует его
по условиям GNU GPL v3.0, что допускается формулировкой «or later».
Исходный код PLDL доступен в этом репозитории; полный текст GPL v3.0
находится в файле [LICENSE](LICENSE).

Если вы распространяете собранный `PLDL.exe` или установщик, сохраняйте
рядом файлы `LICENSE`, `NOTICE` и этот файл, а также ссылку на исходный
код. Это не юридическая консультация: при коммерческом распространении
проверьте требования лицензий самостоятельно.
