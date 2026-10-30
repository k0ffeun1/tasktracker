# Серверный трекер RuFish

Адрес: https://tasks.rufish.app

Сервер: `deploy@217.197.115.153`, SSH-ключ Windows: `~/.ssh/fish_prod`.
DNS: A-запись `tasks` → `217.197.115.153` в зоне `rufish.app`.

Файлы на сервере находятся в `/opt/tasktracker`:

- `server.py` — приложение;
- `compose.yaml` — отдельный Docker Compose проект `tasktracker`;
- `data/tasks.db` — рабочая база SQLite;
- `Caddyfile.before-*` — резервная копия конфигурации до подключения поддомена.

Трекер автоматически запускается после перезагрузки сервера. Порт 8777 доступен
только внутри Docker-сети `fish-app_default`. HTTPS настроен
в `/data/tasktracker/tasks.caddy` внутри контейнера Caddy. Логин и пароль
не запрашиваются: по ссылке доступны просмотр и изменение задач. Файл находится
в постоянном томе `fish-app_caddy_data` и подключается строкой
`import /data/tasktracker/*.caddy` из `/home/deploy/fish-app/Caddyfile`.
Исходник правила — `deploy/tasks.caddy` в этом репозитории.

28.10.2026 перенесён согласованный SQLite-снимок локальной базы: 38 задач.
После переноса серверная и локальная базы независимы; для совместной работы
нужно пользоваться серверным адресом.

Проверка состояния:

```sh
sudo docker compose -p tasktracker -f /opt/tasktracker/compose.yaml ps
sudo docker compose -p tasktracker -f /opt/tasktracker/compose.yaml logs --tail 50
```

Обновление приложения: скопировать новый `server.py` в `/opt/tasktracker/server.py`,
затем выполнить:

```sh
sudo docker compose -p tasktracker -f /opt/tasktracker/compose.yaml restart tasktracker
```

Не заменять рабочую серверную базу локальной при обновлении приложения.
Перед изменениями базы делать резервную копию средствами SQLite (`Connection.backup`).
Базовая конфигурация Caddy хранится в репозитории Fish-android. Подключение
постоянного файла маршрута добавлено туда 29.10.2026: ручной блок потерялся
при `rsync --inplace Caddyfile` из `deploy-api.sh`, что сломало HTTPS поддомена.
Перед деплоем с другой машины нужно подтянуть свежую ветку `v0.6.3`, чтобы
не заменить конфигурацию старой версией без импорта.
