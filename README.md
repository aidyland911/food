# Hamada Sheraton Order App 🥙

Team food-ordering app for Hamada Sheraton. Members log in, pick sandwiches,
and choose whether to tip the delivery man (any amount, e.g. 1.70 EGP). Aidy
(admin) opens/closes ordering rounds and gets the full summary: everyone's
order, sandwich counts for the phone call, and who pays what (food + delivery
split + personal tip).

Full behavior reference: [SPEC.md](SPEC.md). Lab deployment with HTTPS:
[DEPLOY.md](DEPLOY.md).

## Run it

```bash
docker compose up --build -d
```

Open http://localhost:8000 — teammates on the same network use
`http://<your-ip>:8000` (find your IP with `ip addr` / `ipconfig`).

## Accounts

- **Admin:** username `aidy`, password `aidy123` (change via `ADMIN_PASSWORD`
  in `docker-compose.yml` **before first run** — the admin is created once,
  on first startup).
- **Members:** self-register at `/register` with any name + password.
- **Forgotten password:** Aidy → **Admin** → *Reset a member's password*.
  Passwords are hashed and can't be viewed, but a reset keeps the account
  and its order history — no need to register again. Anyone can change
  their own password under **Password** in the top bar.

## How a gathering works

1. Aidy → **Admin** → enters the delivery fee → **Open round**.
2. Everyone opens the link, logs in, adds sandwiches (bread: شامى / بلدى /
   فرنساوى, with سلطة / طحينة checkboxes — untick what you don't want), and
   answers the tip question (yes + amount, or no).
3. Aidy watches the live summary, then **Close round**.
4. The math: each person pays
   `their food + delivery_fee ÷ people_who_ordered + their own tip (if yes)`.
5. Members can review their past orders under **My orders** and, while a
   round is open, hit **Reorder this** on any past round to copy it into the
   current one (charged at today's prices). Aidy can reopen any past round
   summary from the admin page.

## Config (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | dev value | Session signing key — set something random |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `aidy` / `aidy123` | Admin account, created on first run |
| `DB_PATH` | `/data/food.db` | SQLite location (persisted in `./data/`) |
| `SECURE_COOKIES` | unset | Set to `1` to mark session cookies HTTPS-only (the prod compose file does this) |

## Editing the menu

Prices/items live in `menu.json`. **New categories and new items** are added
to the DB automatically on every startup (`docker compose restart` after
editing). **Price changes** to existing items are not synced — to apply them,
delete `./data/food.db` and restart (this wipes users and order history).

## Photos and descriptions

`menu_details.json` holds the English name, description and photo
(`static/items/*.jpg`) for each item, keyed by the Arabic name used in
`menu.json`. Add an entry there (and a square photo) when you add a menu
item; items without one show a placeholder.

## Deploying to the company lab

Copy the repo to the server, adjust `SECRET_KEY`/`ADMIN_PASSWORD`, and run the
same `docker compose up --build -d`. The SQLite volume (`./data`) carries the
users, menu, and history — copy that folder too if you want to keep them.
