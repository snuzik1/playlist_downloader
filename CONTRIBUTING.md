# Участие в проекте

Спасибо за интерес к PLDL. Присылайте bug reports, идеи и pull requests
через GitHub Issues и Pull Requests.

## Требования для разработки

- Windows 10/11 x64;
- Python 3.10+;
- FFmpeg в `PATH` (для ручной проверки MP3/M4A);
- Inno Setup 6 — только для сборки установщика;
- .NET Framework 4.x — только для сборки `PLDL-Uninstall.exe`.

## Быстрый старт

```powershell
git clone <URL-репозитория>
cd playlist_downloader
.\setup.bat
.\run.bat
```

`setup.bat` создаст `.venv` и установит зависимости. `run.bat` запустит
настольное окно; если окружения нет, он вызовет `setup.bat` сам.

## Проверки перед pull request

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py desktop.py pldl.py pldl tests
.\.venv\Scripts\python.exe pldl.py --help
```

Дополнительно вручную проверьте:

- разбор ссылки и список треков в окне;
- загрузку одного трека в MP3 и M4A;
- повторный запуск того же плейлиста (должен пропустить готовые файлы);
- установку, запуск с ярлыка и удаление, если менялся установщик.

## Стиль кода

- Python 3.10+, аннотации типов, 4 пробела, UTF-8.
- Пользовательские строки интерфейса — на русском; сообщения об ошибках
  должны быть понятны без знания кода.
- Не добавляйте сетевые тесты: для платформенных резолверов используйте
  моки или локальные фикстуры.
- Не коммитьте `settings.json`, `pldl.log`, `.cache/`, `dist/`, exe и
  другие артефакты сборки.
- Не добавляйте реальные токены, cookies, Client ID/Secret и приватные
  ссылки ни в код, ни в примеры, ни в issue.

## Pull request

В описании укажите:

- что изменено и зачем;
- как проверено (команды, сценарии, ОС);
- какие ограничения или миграции есть;
- связанные issue, если они есть.

Небольшие PR проще проверять. Если меняется поведение загрузки или
формат настроек, обновите README и CHANGELOG.

## Релиз

1. Обновите версию в `pldl/__init__.py` и
   `uninstaller/Program.cs`.
2. Добавьте запись в `CHANGELOG.md`.
3. Убедитесь, что тесты проходят.
4. Создайте и отправьте тег, совпадающий с версией:

   ```powershell
   git tag v0.1.0
   git push origin v0.1.0
   ```

Workflow `.github/workflows/release.yml` соберёт `PLDL.exe`, установщик,
`PLDL-Uninstall.exe` и `SHA256SUMS.txt`, затем создаст GitHub Release.
Тег должен совпадать с `pldl.__version__`.

## Лицензия

Отправляя изменения, вы соглашаетесь, что они распространяются под
GNU GPL v3.0 (`GPL-3.0-only`), как и остальной проект.
