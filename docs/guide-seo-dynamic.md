# Dynamic SEO for admin guide pages

Guide pages created in **Настройки → Наполнение сайта** are indexed without rebuilding the cabinet.

## How it works

1. Admin saves page → PostgreSQL (`guide_pages`)
2. User or crawler opens `https://example.com/my-new-guide`
3. **Caddy** proxies HTML requests to the bot: `GET /cabinet/seo/page/my-new-guide`
4. Bot reads `index.html` shell from `/srv/cabinet`, injects `meta_title`, `meta_description`, article body from DB
5. Browser loads the same SPA; React hydrates as usual

## Bot endpoints

| URL | Description |
|-----|-------------|
| `/cabinet/seo/page/{slug}` | Single guide page HTML |
| `/cabinet/seo/guides` | `/guides` hub HTML |

## Env (bot `.env`)

```env
CABINET_STATIC_PATH=/srv/cabinet
```

## Caddy (merge into site block)

```caddyfile
@seo_guides {
    path /guides
    header Accept *text/html*
}
handle @seo_guides {
    reverse_proxy localhost:8080 {
        rewrite * /cabinet/seo/guides
        @missing status 404
        handle_response @missing {
            root * /srv/cabinet
            rewrite * /index.html
            file_server
        }
    }
}

@seo_guide_page {
    path_regexp slug ^/[a-z0-9-]+$
    not path /login /admin /buy /news /info /subscriptions /balance /profile /connection /gift /contests /polls /pricing /merge /auto-login /link-telegram /server-status /routers /privacy /offer /proxy /wheel /support /referral /connected-accounts /saved-cards /deep-link /telegram-redirect /cabinet /miniapp /assets/* /pic/* /globe/*
    header Accept *text/html*
}
handle @seo_guide_page {
    reverse_proxy localhost:8080 {
        rewrite * /cabinet/seo/page{path}
        @missing status 404
        handle_response @missing {
            root * /srv/cabinet
            rewrite * /index.html
            file_server
        }
    }
}
```

Place these **before** the static `file_server` handle. Reload: `caddy reload --config /etc/caddy/Caddyfile`

## Deploy checklist

1. Deploy bot with new SEO routes (`docker compose up -d --build bot`)
2. Set `CABINET_STATIC_PATH=/srv/cabinet` in bot `.env`
3. Update Caddy config and reload
4. Deploy cabinet with dynamic `/:pageId` route (`npm run build`)

## Verify

```bash
curl -sI https://example.com/my-slug | head -3
curl -s https://example.com/my-slug | grep -o '<title>[^<]*</title>'
```

After creating a page in admin, the title from **SEO title** should appear immediately.
