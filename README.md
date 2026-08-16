<p align="center">
  <img src="assets/logo.png" alt="VPN BRO" width="128" />
</p>

# VPN BRO Bot

Система для продажи подписок в Telegram и вебе с веб-админкой, интегрированная с Remnawave 3.0.0+ ([docs.rw](https://docs.rw)).

Cabinet API работает на порту `:8080`.

| Репозиторий | Назначение |
|-------------|------------|
| **bot** (этот) | Telegram-бот + Cabinet API |
| [cabinet](https://github.com/dabroivanov-ship-it/cabinet) | Веб-кабинет |

---

## Возможности

### Подписки
- Тарифы на разные сроки, мультитариф
- Трафик: безлимит / лимит / пакеты с докупкой
- Лимит устройств или без ограничений
- Выбор серверов: авто (тариф) или вручную (конфигуратор)
- Триал (в т.ч. платный) → переход на платную подписку
- Корзина при нехватке баланса, автопродление, подарки
- Веб-лендинги для продажи без входа в кабинет

### Платежи
- Много провайдеров параллельно (карты, СБП, Stars, крипто)
- Единый баланс → покупка подписки
- Автопокупка после пополнения, сохранённые карты (рекуррент)
- Фискализация НалоGo, гостевые оплаты с лендингов

Основные: **YooKassa**, **Telegram Stars**, **CryptoBot**, **Platega**, **MulenPay**, **WATA**, **Tribute** и другие — см. `.env.example`.

### Маркетинг
- Промокоды, рефералка и партнёрка с выводом
- Рассылки по сегментам, кампании с трекингом
- Лендинги, конкурсы, персональные скидки
- Обязательная подписка на каналы

### Веб
- Сайт + личный кабинет + Telegram Mini App
- Гайды / база знаний из админки, SEO-страницы
- Статус серверов, магазин роутеров
- Рекомендуемые клиенты на главной (Incy / Happ)

### Админка
- Telegram и веб-панель, RBAC
- Пользователи, платежи, балансы, автоплатежи
- Уведомления в топики (покупки, тикеты, ноды, биллинг Remnawave)
- Мониторинг трафика, техработы, бэкапы БД

---

## Изменения VPN BRO

Кастом относительно upstream Bedolaga / Remnawave-стека. Сайт: [vpnbro.pro](https://vpnbro.pro).

### Кабинет API и контент
- CMS гайдов / базы знаний, SEO HTML из БД
- Рекомендуемые клиенты на главной (Incy / Happ)
- Биллинг Remnawave: серверы, оплаты, просрочки; уведомления в Telegram-топик
- Кампании: веб-ссылка `/login?campaign=`, бонусы без нулевого баланса
- Домен vpnbro.pro: URL кабинета, CORS, return URL платежей

### Платежи и финансы
- Списание с сохранённой карты при пополнении баланса
- Автоплатежи, финансы в админке, налоги / NaloGO
- Лендинги и гостевые покупки

### Прочее
- Магазин роутеров, рефералка / партнёрка
- WebSocket для тикетов
- Rate limit публичного статуса серверов
- Исправления Remnawave 3.x (id трафика / сброс)

Подробнее по датам — в [`CHANGELOG.md`](CHANGELOG.md).

---

## Требования

- Linux-сервер с **Docker** и **Docker Compose**
- Токен бота от [@BotFather](https://t.me/BotFather)
- Доступ к панели Remnawave
- Для кабинета: свой домен + обратный прокси (Caddy / Nginx)

---

## Установка

### 1. Клонирование и `.env`

```bash
cd /root
git clone https://github.com/dabroivanov-ship-it/bot.git vpnbro-bot
cd vpnbro-bot

cp .env.example .env
```

Заполните минимум: `BOT_TOKEN`, `ADMIN_IDS`, `REMNAWAVE_*`, при необходимости блок `CABINET_*` и платежи — см. `.env.example`.

### 2. Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/health
# ожидается 200, 401 или 403
docker compose logs bot --since 2m | tail -30
```

Дальше — сборка и прокси фронта: [инструкция в cabinet](https://github.com/dabroivanov-ship-it/cabinet#установка).

---

## Обновление

```bash
cd /root/vpnbro-bot
git pull origin main
docker compose up -d --build bot
```

Миграции БД применяются автоматически при старте контейнера.

---

## Полезные команды

```bash
# Статус
docker compose ps

# Логи бота
docker compose logs bot -f --tail 100

# Перезапуск только бота
docker compose restart bot

# Полный перезапуск стека
docker compose down && docker compose up -d
```

---

## Переменные окружения (кабинет)

| Переменная | Описание |
|------------|----------|
| `CABINET_ENABLED` | Включить Cabinet API |
| `CABINET_URL` | Публичный URL кабинета (`https://example.com`) |
| `CABINET_JWT_SECRET` | Секрет JWT (`openssl rand -hex 32`) |
| `CABINET_ALLOWED_ORIGINS` | CORS (ваш домен кабинета) |

Полный список — в `.env.example`.
