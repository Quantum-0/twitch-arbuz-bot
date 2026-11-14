# MemeAlerts External API (`ext-api`)

Документация по публичному API MemeAlerts для сторонних приложений. API построено по образцу
[DonationAlerts API](https://www.donationalerts.com/apidoc): OAuth 2.0 (Authorization Code) для авторизации и
Centrifugo для доставки real-time событий о донатах.

- [Введение](#введение)
  - [HTTP API запросы](#http-api-запросы)
    - [Запросы и ответы](#запросы-и-ответы)
    - [Ошибки и статусы](#ошибки-и-статусы)
  - [Centrifugo](#centrifugo)
    - [Шаги подключения к WebSocket-серверу Centrifugo](#шаги-подключения-к-websocket-серверу-centrifugo)
    - [1. Подключение к WebSocket-серверу и получение Client ID](#1-подключение-к-websocket-серверу-centrifugo-и-получение-client-id)
    - [2. Подписка на приватные каналы и получение токенов](#2-подписка-на-приватные-каналы-и-получение-токенов)
    - [3. Подключение к приватным каналам](#3-подключение-к-приватным-каналам)
- [Авторизация](#авторизация)
  - [Access Token](#access-token)
  - [Scopes](#scopes)
  - [Grant Type: Authorization Code](#grant-type-authorization-code)
    - [Шаги авторизации](#шаги-авторизации)
    - [1. Регистрация приложения](#1-регистрация-приложения)
    - [2. Запрос авторизации](#2-запрос-авторизации)
    - [3. Получение Authorization Code](#3-получение-authorization-code)
    - [4. Получение Access Token](#4-получение-access-token)
    - [Обновление Access Token](#обновление-access-token)
- [API v1](#api-v1)
  - [Управление приложениями](#управление-приложениями)
  - [Пользователи](#пользователи)
    - [Информация о профиле](#информация-о-профиле)
    - [Выдача бонуса](#выдача-бонуса)
  - [Каналы Centrifugo](#каналы-centrifugo)
    - [Новые донаты](#новые-донаты)
    - [Отправки стикеров](#отправки-стикеров)

---

# Введение

Публичный API MemeAlerts организован вокруг REST. URL ресурсо-ориентированные, ответы — в формате JSON,
используются стандартные HTTP-коды и методы.

## HTTP API запросы

Базовый адрес API — `https://memealerts.com`.

Все методы API v1 расположены под префиксом `/api/v1`. OAuth-эндпоинты — под `/oauth` и `/api/oauth`.

### Запросы и ответы

Все ответы предоставляются в формате JSON. В отдельных случаях может возвращаться код `204 No Content`
с пустым телом.

Пример запроса:

```
curl \
    -X GET https://memealerts.com/api/v1/user/oauth \
    -H "Authorization: Bearer <token>"
```

Пример ответа:

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "data": {
        "id": "633edfa2e6cb81985b72e7aa",
        "code": "ivan",
        "name": "Ivan",
        "avatar": "https://memealerts.com/media/avatars/ivan.png",
        "email": "ivan@example.com",
        "socket_connection_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "channel": {
            "unique_link": "ivan",
            "currency_name_declensions": {
                "genitive": "Мемкоина",
                "dative": "Мемкоину",
                "accusative": "Мемкоин",
                "instrumental": "Мемкоином",
                "prepositional": "Мемкоине",
                "multiple": {
                    "nominative": "Мемкоины",
                    "genitive": "Мемкоинов",
                    "dative": "Мемкоинам",
                    "accusative": "Мемкоины",
                    "instrumental": "Мемкоинами",
                    "prepositional": "Мемкоинах"
                }
            },
            "disable_stickers": false,
            "welcome_bonus_enabled": true
        }
    }
}
```

### Ошибки и статусы

API использует стандартные HTTP-коды для индикации успеха или ошибки запроса. Коды `2xx` — успех,
`4xx` — ошибка на стороне клиента (например, не передан обязательный параметр или невалидный токен),
`5xx` — ошибка на стороне сервера.

| Статус | Описание |
|--------|----------|
| 200 OK | Стандартный ответ для успешного запроса |
| 201 Created | Запрос выполнен, создан новый ресурс |
| 204 No Content | Запрос выполнен, тело ответа пустое |
| 302 Found | Редирект (используется в OAuth-флоу) |
| 400 Bad Request | Ошибка в запросе клиента (неверные/отсутствующие параметры) |
| 401 Unauthorized | Требуется аутентификация, либо токен невалиден или не имеет нужного scope |
| 404 Not Found | Запрошенный ресурс не найден |
| 500 Internal Server Error | Внутренняя ошибка сервера |

Пример ошибки:

```
HTTP/1.1 401 Unauthorized
Content-Type: application/json
```

```
{
    "message": "Unauthenticated."
}
```

## Centrifugo

Для доставки real-time уведомлений (о новых донатах и об отправках стикеров) используется
[Centrifugo](https://centrifugal.dev/) **версии 5 (v5)**. Он работает как отдельный сервер и держит
постоянные соединения с приложениями.

> Используется Centrifugo v5, поэтому на стороне клиента необходимо использовать совместимый с v5
> SDK (например, `centrifuge-js` версии 5.x) и соответствующий v5 протокол подключения/подписки.

WebSocket endpoint Centrifugo:

```
wss://memealerts.com/connection/websocket
```

Подписка на приватные каналы Centrifugo должна быть подписана API-приложением — токен подписки выдаётся
методом [`POST /api/v1/centrifuge/subscribe`](#2-подписка-на-приватные-каналы-и-получение-токенов).

### Шаги подключения к WebSocket-серверу Centrifugo

1. Подключение к WebSocket-серверу и получение Client ID (UUIDv4).
2. Подписка на приватные каналы и получение токенов подписки.
3. Подключение к приватным каналам.

### 1. Подключение к WebSocket-серверу Centrifugo и получение Client ID

Сначала откройте соединение с WebSocket endpoint. После открытия соединения отправьте сообщение,
содержащее `id` сообщения и `socket_connection_token`, полученный ранее запросом
[`GET /api/v1/user/oauth`](#информация-о-профиле). Сообщение в формате JSON:

```
{
    "params": {
        "token": "<socket_connection_token>"
    },
    "id": 1
}
```

В ответ придёт сообщение со сгенерированным Client ID в формате UUIDv4 и версией Centrifugo:

```
{
    "id": 1,
    "result": {
        "client": "d558c046-c679-43e3-a62d-65989ab55f7c",
        "version": "5.x.x"
    }
}
```

### 2. Подписка на приватные каналы и получение токенов

После получения Client ID можно подписаться на нужные каналы:

`POST https://memealerts.com/api/v1/centrifuge/subscribe`

Параметры запроса:

| Поле | Тип | Описание |
|------|-----|----------|
| channels | string[] | Массив имён приватных каналов для подписки. Обязательно |
| client | string | Centrifugo UUIDv4 Client ID, полученный при подключении к WebSocket-серверу. Обязательно |

Требует авторизации. Набор каналов, на которые можно подписаться, определяется выданными токену scope:
канал донатов (`$alerts:donation_<user_id>`) доступен со scope `oauth-donation-subscribe`, канал отправок
стикеров (`$alerts:sticker_<user_id>`) — со scope `oauth-sticker-subscribe`.

```
curl \
    -X POST https://memealerts.com/api/v1/centrifuge/subscribe \
    -H "Authorization: Bearer <token>" \
    -H "Content-Type: application/json" \
    -d '{"channels":["$alerts:donation_<user_id>"], "client":"<uuidv4_client_id>"}'
```

Параметры ответа (для каждого канала):

| Поле | Тип | Описание |
|------|-----|----------|
| channel | string | Имя приватного канала |
| token | string | Токен подписки для подключения к этому каналу |

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "channels": [
        {
            "channel": "$alerts:donation_<user_id>",
            "token": "<subscription_token>"
        }
    ]
}
```

> Приложение может подписываться только на каналы авторизованного пользователя
> (`$alerts:donation_<user_id>` и/или `$alerts:sticker_<user_id>`, где `<user_id>` — `id` из ответа
> `/api/v1/user/oauth`) и только при наличии соответствующего scope. Остальные запрошенные каналы будут отброшены.

### 3. Подключение к приватным каналам

Чтобы подключиться к подписанным каналам, отправьте ещё одно сообщение по уже открытому WebSocket-соединению.
Каждое сообщение содержит имя канала и токен подписки:

```
{
    "params": {
        "channel": "$alerts:donation_<user_id>",
        "token": "<subscription_token>"
    },
    "method": 1,
    "id": 2
}
```

После этого вы получите подтверждение успешного подключения к каналу и начнёте получать real-time события.

---

# Авторизация

Используется протокол OAuth 2.0 (см. [RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749)).
Все методы API v1 требуют авторизации.

## Access Token

Access token — это то, что приложение использует для запросов к API от имени пользователя. Токен представляет
авторизацию конкретного приложения на доступ к конкретным данным пользователя. Access token необходимо хранить
конфиденциально и передавать только по HTTPS.

| Токен | Время жизни (по умолчанию) |
|-------|----------------------------|
| Authorization code | 10 минут, одноразовый |
| Access token | 1 час |
| Refresh token | 30 дней |

## Scopes

Scope ограничивает доступ приложения к аккаунту пользователя. Приложение запрашивает один или несколько scope,
они показываются пользователю на экране согласия, и выданный access token будет ограничен этими scope.

MemeAlerts предоставляет сторонним приложениям следующие scope:

| Scope | Описание |
|-------|----------|
| `oauth-user-show` | Получить данные профиля и `socket_connection_token` |
| `oauth-donation-subscribe` | Подписаться на уведомления о новых донатах через Centrifugo |
| `oauth-sticker-subscribe` | Подписаться на уведомления об отправках стикеров через Centrifugo |
| `oauth-bonus-give` | Выдавать бонус зрителю от имени стримера |

## Grant Type: Authorization Code

Используется Authorization Code grant — он оптимизирован для серверных приложений, где исходный код не раскрыт
публично и можно безопасно хранить `client_secret`. Это flow с редиректами: приложение должно уметь
взаимодействовать с user-agent и получать authorization code через него.

### Шаги авторизации

1. Регистрация приложения.
2. Запрос авторизации.
3. Получение authorization code.
4. Обмен authorization code на access token.

### 1. Регистрация приложения

**ВНИМАНИЕ!**

**API находится в режиме тестирования.**
**Регистрация приложений производится путем обращения в поддержку.**

Зарегистрируйте приложение методом [`POST /api/oauth/apps`](#управление-приложениями) (требуется авторизация
пользователя MemeAlerts по JWT). В ответ сервис выдаёт `clientId` и `clientSecret`. `clientId` — публичный
идентификатор, используемый для построения authorization URL. `clientSecret` используется для аутентификации
приложения при обмене кода на токен и должен храниться в секрете.

> `clientSecret` показывается **только один раз** — при регистрации. Сохраните его.

### 2. Запрос авторизации

Пользователю даётся ссылка (или редирект) на `https://memealerts.com/oauth/authorize` с параметрами
`client_id`, `redirect_uri`, `response_type` и `scope`.

`GET https://memealerts.com/oauth/authorize`

Параметры запроса:

| Query String | Тип | Описание |
|--------------|-----|----------|
| client_id | string | Client ID, полученный от MemeAlerts. Обязательно |
| redirect_uri | string | URL, на который пользователь будет перенаправлен после авторизации. Должен совпадать с одним из зарегистрированных. Обязательно |
| response_type=code | string | Указывает, что приложение запрашивает authorization code grant. Обязательно |
| scope | string | Список scope через пробел. Обязательно |
| state | string | Произвольная строка, возвращается без изменений в `redirect_uri`. Рекомендуется для защиты от CSRF |

После валидации параметров сервис перенаправляет пользователя на страницу согласия, где пользователь авторизуется и подтверждает или отклоняет доступ.

После подтверждения согласия frontend отправляет:

`POST https://memealerts.com/oauth/authorize` (с JWT пользователя MemeAlerts)

| Поле | Тип | Описание |
|------|-----|----------|
| client_id | string | Client ID. Обязательно |
| redirect_uri | string | Зарегистрированный redirect URI. Обязательно |
| scope | string | Список scope через пробел. Обязательно |
| state | string | Та же строка `state`, что и в запросе авторизации |

Параметры ответа:

| Поле | Тип | Описание |
|------|-----|----------|
| returnUrl | string | Полный URL для редиректа на `redirect_uri` приложения с query-параметрами `code` и `state` (или `error` и `state` при сбое). Обязательно |

При успешном подтверждении:

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "returnUrl": "https://my-app.com/callback?code=<authorization_code>&state=<state>"
}
```

При сбое выдачи authorization code:

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "returnUrl": "https://my-app.com/callback?error=server_error&state=<state>"
}
```

Frontend consent после получения ответа выполняет редирект самостоятельно, например:
`window.location.href = response.returnUrl`.

### 3. Получение Authorization Code

Если пользователь разрешает доступ, `returnUrl` содержит `redirect_uri` приложения вместе с
authorization code. Код доступен как значение параметра `code` в query string:

```
<redirect_uri>?code=<authorization_code>&state=<state>
```

### 4. Получение Access Token

Authorization code обменивается на access token на `https://memealerts.com/oauth/token` с параметрами
`grant_type`, `client_id`, `client_secret`, `redirect_uri` и `code`.

`POST https://memealerts.com/oauth/token`

Параметры запроса:

| Поле | Тип | Описание |
|------|-----|----------|
| grant_type=authorization_code | string | Тип гранта. Обязательно |
| client_id | string | Client ID приложения. Обязательно (либо в заголовке `Authorization: Basic`) |
| client_secret | string | Client Secret приложения. Обязательно (либо в заголовке `Authorization: Basic`) |
| redirect_uri | string | URL редиректа (должен совпадать с использованным в запросе авторизации). Обязательно |
| code | string | Authorization code. Обязательно |

```
curl \
    -X POST https://memealerts.com/oauth/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=authorization_code&client_id=<client_id>&client_secret=<client_secret>&redirect_uri=<redirect_uri>&code=<code>"
```

Authorization code одноразовый и живёт 10 минут. Повторный обмен того же кода возвращает
`invalid_grant`, поэтому при ретрае запроса нужно начинать авторизацию заново.

Параметры ответа:

| Поле | Тип | Описание |
|------|-----|----------|
| token_type | string | Тип токена (`Bearer`). Обязательно |
| expires_in | integer | Количество секунд до истечения access token. Обязательно |
| access_token | string | Access token. Обязательно |
| refresh_token | string | Refresh token. Обязательно |

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "token_type": "Bearer",
    "expires_in": 3600,
    "access_token": "a1b2c3d4e5f6...",
    "refresh_token": "f6e5d4c3b2a1..."
}
```

### Обновление Access Token

Refresh Token grant используется для обмена refresh token на новый access token, когда срок действия access
token истёк. При успешной валидации сервис генерирует новый access token и новый refresh token, а старая
пара переходит в grace-период (см. ниже).

`POST https://memealerts.com/oauth/token`

Параметры запроса:

| Поле | Тип | Описание |
|------|-----|----------|
| grant_type=refresh_token | string | Тип гранта. Обязательно |
| refresh_token | string | Refresh token, полученный ранее. Обязательно |
| client_id | string | Client ID приложения. Обязательно (либо в заголовке `Authorization: Basic`) |
| client_secret | string | Client Secret приложения. Обязательно (либо в заголовке `Authorization: Basic`) |

```
curl \
    -X POST https://memealerts.com/oauth/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=refresh_token&refresh_token=<refresh_token>&client_id=<client_id>&client_secret=<client_secret>"
```

```
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store
```

```
{
    "token_type": "Bearer",
    "expires_in": 3600,
    "access_token": "newaccess...",
    "refresh_token": "newrefresh..."
}
```

#### Ротация refresh token и grace-период

Refresh token одноразовый: **каждый успешный запрос возвращает новый `refresh_token`, и его обязательно
нужно сохранить вместо старого**. Access token из предыдущей пары после обновления живёт ещё 60 секунд,
чтобы уже отправленные запросы успели завершиться.

В течение тех же 60 секунд повторный запрос с уже обменянным refresh token возвращает `200 OK` с той же
парой токенов, что и первый запрос. Это сделано специально, чтобы сетевой ретрай или гонка параллельных
воркеров не рвали сессию. По истечении grace-периода такой запрос вернёт `invalid_grant`, и потребуется
повторная авторизация пользователя.

Практические следствия для клиента: обновляйте токен в одном месте и под блокировкой (иначе несколько
воркеров начнут обновлять одну и ту же пару), сохраняйте новый `refresh_token` до того, как начнёте им
пользоваться, и не считайте `400` поводом повторить тот же запрос — сначала посмотрите на поле `error`.

### Ошибки token endpoint

Ошибки возвращаются в формате RFC 6749 §5.2: код в поле `error`, человекочитаемое пояснение
в `error_description`.

```
HTTP/1.1 400 Bad Request
Content-Type: application/json
Cache-Control: no-store
```

```
{
    "error": "invalid_grant",
    "error_description": "Refresh token has already been exchanged. Use the refresh token returned by the previous refresh request."
}
```

| `error` | HTTP | Когда возникает и что делать |
|---------|------|------------------------------|
| invalid_request | 400 | Не хватает обязательного параметра или credentials переданы двумя способами сразу и не совпадают. Починить запрос |
| invalid_client | 401 | Неизвестный `client_id`, неверный `client_secret` или приложение деактивировано. Проверить креды и обратиться к нам |
| invalid_grant | 400 | Код или refresh token неизвестен, просрочен, уже использован или отозван пользователем. Нужна повторная авторизация пользователя (кроме случая просроченного `code` — там достаточно начать флоу заново) |
| unsupported_grant_type | 400 | Поддерживаются только `authorization_code` и `refresh_token` |
| server_error | 400 | Временный сбой на нашей стороне. Старый refresh token при этом остаётся валидным, запрос можно безопасно повторить |

Ошибку `invalid_grant` **нельзя** ретраить с теми же параметрами: она означает, что грант больше
не существует. Ошибку `server_error` ретраить можно и нужно.

---

# API v1

## Управление приложениями

Методы управления OAuth-приложениями. Требуют авторизации пользователя MemeAlerts по JWT
(`Authorization: Bearer <ma_jwt>`). Приложение привязывается к пользователю-владельцу.

### Зарегистрировать приложение

`POST https://memealerts.com/api/oauth/apps`

| Поле | Тип | Описание |
|------|-----|----------|
| name | string | Название приложения. Обязательно |
| redirectUris | string[] | Список разрешённых redirect URI. Обязательно, минимум один |
| allowedScopes | string[] | Список запрашиваемых scope (см. [Scopes](#scopes)) |

```
curl \
    -X POST https://memealerts.com/api/oauth/apps \
    -H "Authorization: Bearer <ma_jwt>" \
    -H "Content-Type: application/json" \
    -d '{"name":"My App","redirectUris":["https://my-app.com/callback"],"allowedScopes":["oauth-user-show","oauth-donation-subscribe"]}'
```

```
HTTP/1.1 201 Created
Content-Type: application/json
```

```
{
    "clientId": "5f1c2e6a-8b3d-4c7e-9a1b-2c3d4e5f6a7b",
    "clientSecret": "9f8e7d6c5b4a...",
    "name": "My App",
    "redirectUris": ["https://my-app.com/callback"],
    "allowedScopes": ["oauth-user-show", "oauth-donation-subscribe"]
}
```

> `clientSecret` возвращается только при создании. Сохраните его в надёжном месте.

### Список приложений

`GET https://memealerts.com/api/oauth/apps`

Возвращает список приложений текущего пользователя (без `clientSecret`).

```
[
    {
        "clientId": "5f1c2e6a-8b3d-4c7e-9a1b-2c3d4e5f6a7b",
        "name": "My App",
        "redirectUris": ["https://my-app.com/callback"],
        "allowedScopes": ["oauth-user-show", "oauth-donation-subscribe"],
        "isActive": true
    }
]
```

### Удалить приложение

`DELETE https://memealerts.com/api/oauth/apps/{clientId}`

```
{
    "deleted": true
}
```

## Пользователи

### Информация о профиле

Возвращает информацию о профиле пользователя. Требует авторизации со scope `oauth-user-show`.

`GET https://memealerts.com/api/v1/user/oauth`

```
curl \
    -X GET https://memealerts.com/api/v1/user/oauth \
    -H "Authorization: Bearer <token>"
```

Параметры ответа:

| Поле | Тип | Описание |
|------|-----|----------|
| id | string | Уникальный неизменяемый идентификатор пользователя. Обязательно |
| code | string | Уникальное имя пользователя (username). Обязательно |
| name | string | Отображаемое имя пользователя. Обязательно |
| avatar | string | URL аватара пользователя. Обязательно |
| email | string | Email пользователя. Обязательно |
| socket_connection_token | string | Токен подключения к Centrifugo. Обязательно |
| channel | object | Данные канала стримера (см. ниже). Обязательно |

Ресурс канала (`channel`):

| Поле | Тип | Описание |
|------|-----|----------|
| unique_link | string, null | Публичный slug канала — сегмент ссылки на страницу стримера на MemeAlerts (`https://memealerts.com/{unique_link}`). Может измениться при смене ссылки канала |
| currency_name_declensions | object, null | Склонения названия валюты канала (мемкоинов). Нужны для корректных сообщений вида «получает N \<валюта в нужном падеже\>» |
| currency_name_declensions.genitive | string | Родительный падеж ед. числа (1 мемкоина) |
| currency_name_declensions.dative | string | Дательный падеж ед. числа |
| currency_name_declensions.accusative | string | Винительный падеж ед. числа |
| currency_name_declensions.instrumental | string | Творительный падеж ед. числа |
| currency_name_declensions.prepositional | string | Предложный падеж ед. числа |
| currency_name_declensions.multiple | object | Склонения во множественном числе |
| currency_name_declensions.multiple.nominative | string | Именительный падеж мн. числа |
| currency_name_declensions.multiple.genitive | string | Родительный падеж мн. числа (5 мемкоинов) |
| currency_name_declensions.multiple.dative | string | Дательный падеж мн. числа |
| currency_name_declensions.multiple.accusative | string | Винительный падеж мн. числа |
| currency_name_declensions.multiple.instrumental | string | Творительный падеж мн. числа |
| currency_name_declensions.multiple.prepositional | string | Предложный падеж мн. числа |
| disable_stickers | boolean | `true`, если отправка стикеров на канале выключена. По умолчанию `false` |
| welcome_bonus_enabled | boolean | `true`, если приветственный бонус на канале включён. По умолчанию `true` |

> `code` и `name` могут измениться в любой момент при переименовании пользователя.

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "data": {
        "id": "633edfa2e6cb81985b72e7aa",
        "code": "ivan",
        "name": "Ivan",
        "avatar": "https://memealerts.com/media/avatars/ivan.png",
        "email": "ivan@example.com",
        "socket_connection_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "channel": {
            "unique_link": "ivan",
            "currency_name_declensions": {
                "genitive": "Мемкоина",
                "dative": "Мемкоину",
                "accusative": "Мемкоин",
                "instrumental": "Мемкоином",
                "prepositional": "Мемкоине",
                "multiple": {
                    "nominative": "Мемкоины",
                    "genitive": "Мемкоинов",
                    "dative": "Мемкоинам",
                    "accusative": "Мемкоины",
                    "instrumental": "Мемкоинами",
                    "prepositional": "Мемкоинах"
                }
            },
            "disable_stickers": false,
            "welcome_bonus_enabled": true
        }
    }
}
```

### Выдача бонуса

Начисляет бонус (мемкоины) зрителю от имени стримера — авторизованного пользователя, выдавшего токен.
Требует авторизации со scope `oauth-bonus-give`.

`POST https://memealerts.com/api/v1/user/give-bonus`

Параметры запроса:

| Поле | Тип | Описание |
|------|-----|----------|
| userId | string | Идентификатор зрителя-получателя бонуса (`id` пользователя MemeAlerts). Обязательно |
| value | number | Размер бонуса в мемкоинах. Целое положительное число, не больше 1000. Обязательно |

Зритель должен быть саппортером стримера (хотя бы раз поддерживал канал), иначе вернётся ошибка.

```
curl \
    -X POST https://memealerts.com/api/v1/user/give-bonus \
    -H "Authorization: Bearer <token>" \
    -H "Content-Type: application/json" \
    -d '{"userId":"633edfa2e6cb81985b72e7aa","value":50}'
```

```
HTTP/1.1 201 Created
Content-Type: application/json
```

```
{
    "result": true
}
```

### Список саппортеров

Возвращает список саппортеров стримера — авторизованного пользователя, выдавшего токен.
Требует авторизации со scope `oauth-bonus-give`.

`GET https://memealerts.com/api/v1/user/supporters`

Query-параметры (постранично):

| Параметр | Тип | Описание |
|----------|-----|----------|
| skip | number | Сколько записей пропустить. По умолчанию `0` |
| limit | number | Сколько записей вернуть. По умолчанию `20`, максимум `100` |
| query | string | Поиск по имени саппортера (необязательно) |

```
curl \
    -X GET "https://memealerts.com/api/v1/user/supporters?skip=0&limit=20" \
    -H "Authorization: Bearer <token>"
```

Параметры ответа:

| Поле | Тип | Описание |
|------|-----|----------|
| data | array | Список саппортеров (см. ниже) |
| total | number | Общее количество саппортеров (с учётом `query`) |

Ресурс саппортера (`data[]`):

| Поле | Тип | Описание |
|------|-----|----------|
| supporterId | string | Идентификатор пользователя-саппортера (`id` пользователя MemeAlerts) |
| supporterName | string | Отображаемое имя саппортера |
| supporterAvatar | string, null | URL аватара саппортера (CDN) |
| supporterLink | string, null | Ссылка на канал саппортера |
| spent | number | Сколько мемкоинов саппортер потратил у стримера |
| purchased | number | Сколько мемкоинов саппортер приобрёл у стримера |
| joined | number | Дата первого взаимодействия (Unix timestamp, мс) |
| mutedByStreamer | boolean | Заблокирован ли саппортер стримером |

```
HTTP/1.1 200 OK
Content-Type: application/json
```

```
{
    "data": [
        {
            "supporterId": "633edfa2e6cb81985b72e7aa",
            "supporterName": "Ivan",
            "supporterAvatar": "https://cdns.memealerts.com/avatars/ivan.png",
            "supporterLink": "ivan",
            "spent": 1500,
            "purchased": 2000,
            "joined": 1717400000000,
            "mutedByStreamer": false
        }
    ],
    "total": 1
}
```

## Каналы Centrifugo

MemeAlerts предоставляет канал Centrifugo для получения real-time уведомлений о событиях.
Подробнее о Centrifugo — в разделе [Centrifugo](#centrifugo).

Каждое сообщение в канал содержит атрибут `reason`, описывающий произошедшее событие, и сам ресурс в поле `data`.

### Новые донаты

Подписка на этот канал позволяет получать уведомления о новых донатах пользователя. Требует авторизации со
scope `oauth-donation-subscribe`.

Имя канала Centrifugo — `$alerts:donation_<user_id>`, где `<user_id>` — `id` из ответа
[`/api/v1/user/oauth`](#информация-о-профиле).

Событие отправляется только после того, как донат фактически доставлен зрителю — то есть после прохождения
премодерации (если она включена для доната) либо сразу, если премодерация не требуется.

Сообщение, отправляемое в канал:

| Поле | Тип | Описание |
|------|-----|----------|
| reason | string | Причина события. `new` — новый донат |
| data | object | Ресурс доната (см. ниже) |

Ресурс доната (`data`):

| Поле | Тип | Описание |
|------|-----|----------|
| id | string | Уникальный идентификатор доната |
| username | string | Имя пользователя, отправившего донат |
| message | string, null | Сообщение, отправленное вместе с донатом |
| amount | number | Сумма доната |
| type | string | Тип валюты доната: `coins` (мемкоины) или `currency` (деньги) |
| createdAt | number | Дата и время создания доната (Unix timestamp, мс) |
| goalTitle | string | Название цели, если донат был отправлен в цель |
| currencyName | string | Название валюты в нужном склонении (в зависимости от `amount`): для доната в мемкоинах (`coins`) — валюта стримера, для доната в деньгах (`currency`) — «рубль/рубля/рублей» |
| currencyCustomImageUrl | string | CDN-ссылка на кастомную иконку валюты стримера. Возвращается только для донатов в мемкоинах (`coins`) и если иконка задана; для донатов в деньгах (`currency`) — отсутствует |

Пример сообщения:

```
{
    "reason": "new",
    "data": {
        "id": "6650a1b2c3d4e5f6a7b8c9d0",
        "username": "Ivan",
        "message": "Hello!",
        "amount": 500,
        "type": "currency",
        "createdAt": 1717400000000,
        "goalTitle": "На новый микрофон",
        "currencyName": "рублей"
    }
}
```

### Отправки стикеров

Подписка на этот канал позволяет получать уведомления об отправках стикеров пользователю. Требует авторизации
со scope `oauth-sticker-subscribe`.

Имя канала Centrifugo — `$alerts:sticker_<user_id>`, где `<user_id>` — `id` из ответа
[`/api/v1/user/oauth`](#информация-о-профиле).

Обрабатываются три типа отправок: обычный стикер, фуллскрин-стикер и мемпушка (massed-отправка). Событие
отправляется только после того, как стикер фактически доставлен стримеру — то есть после прохождения
премодерации (если она включена) либо сразу, если премодерация не требуется.

Сообщение, отправляемое в канал:

| Поле | Тип | Описание |
|------|-----|----------|
| reason | string | Причина события. `new` — новая отправка стикера |
| data | object | Ресурс отправки стикера (см. ниже) |

Ресурс отправки стикера (`data`):

| Поле | Тип | Описание |
|------|-----|----------|
| id | string | Уникальный идентификатор события |
| type | string | Тип отправки: `STICKER` (стикер), `FULLSCREEN_STICKER` (фуллскрин-стикер), `MEME_CANNON` (мемпушка) |
| timestamp | number | Дата и время отправки (Unix timestamp, мс) |
| sender | object | Информация об отправителе: `id` (идентификатор пользователя), `name` (имя) и `avatar` (URL аватара) |
| stickerName | string | Название стикера |
| stickerUrl | string, null | Ссылка на стикер (CDN) |
| price | number | Стоимость отправки в мемкоинах |
| count | number | Количество стикеров (только для мемпушки `MEME_CANNON`) |
| currencyName | string | Название валюты стримера в нужном склонении (в зависимости от `price`) |
| currencyCustomImageUrl | string | CDN-ссылка на кастомную иконку валюты стримера, если она задана |
| isMemeParty | boolean | Признак мемпати (`true`, если стоимость равна 0) |
| isSoundOnly | boolean | Отправлен только звук |

Пример сообщения:

```
{
    "reason": "new",
    "data": {
        "id": "6650a1b2c3d4e5f6a7b8c9d0",
        "type": "MEME_CANNON",
        "timestamp": 1717400000000,
        "sender": {
            "id": "633edfa2e6cb81985b72e7aa",
            "name": "Ivan",
            "avatar": "https://cdns.memealerts.com/avatars/ivan.png"
        },
        "stickerName": "Pog",
        "stickerUrl": "https://cdns.memealerts.com/stickers/pog.webm",
        "price": 50,
        "count": 10,
        "currencyName": "мемкоинов",
        "currencyCustomImageUrl": "https://cdns.memealerts.com/currency/coin.png",
        "isMemeParty": false,
        "isSoundOnly": false
    }
}
```
