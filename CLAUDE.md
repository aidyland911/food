# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Team food-ordering app for the Hamada Sheraton restaurant: a single Flask file (`app.py`) + SQLite, server-rendered Jinja templates, no JS framework, no ORM. `SPEC.md` is the authoritative behavior reference — read it before changing ordering, pricing, tip, or reorder logic, and update it when behavior changes. `README.md` is the user-facing guide; `DEPLOY.md` covers the HTTPS lab deployment.

## Commands

```bash
# Local dev (Flask debug server on :8000, DB at ./food.db in the cwd)
.venv/bin/python app.py

# Local via Docker (gunicorn, DB persisted in ./data/food.db)
docker compose up --build -d

# Production stack (nginx TLS proxy + app; requires .env with SECRET_KEY and ADMIN_PASSWORD)
docker compose -f docker-compose.prod.yml up --build -d
```

Use the project venv (`.venv/bin/python`), not system Python. There is no test suite and no linter configured.

Default admin login for local runs: `aidy` / `aidy123`.

## Architecture notes

- **Everything is in `app.py`**: schema (`SCHEMA` string), startup migration + seeding (`init_db()`, called at import time so it also runs under gunicorn), auth decorators, helpers, and all routes. Mutations are plain POST forms that flash a message and redirect.
- **Schema changes** have no migration framework. `CREATE TABLE IF NOT EXISTS` won't alter existing DBs, so new columns need an explicit `ALTER TABLE` guard in `init_db()` (see the `salad`/`tahini` precedent) — the production DB has real users and history.
- **Prices are never stored on order lines.** Every total (cart, admin summary, history, reorder) joins the current `items` table via `order_lines()`. A price change therefore rewrites what past rounds display.
- **`menu.json` sync is additive only.** On startup, new categories/items are inserted; existing rows are matched by Arabic name and left untouched. Editing a price in `menu.json` does nothing for an existing item — the DB row must be updated too (or the DB recreated, which wipes users and history). Renaming an item in `menu.json` creates a new item rather than renaming the old one.
- **Rounds**: at most one open at a time; `open_round()` is the gate every order mutation checks. Closing a round freezes its orders.
- **Orders**: one per user per round, created lazily by `get_or_create_order()`. `add_order_line()` merges lines with identical item/bread/salad/tahini by summing qty — reorder relies on this too.
- **Money math** lives in `round_summary()`: `person_total = food + delivery_fee / participants + own tip`, where participants are users with at least one order line (a tip alone doesn't count). The `/` route recomputes the share separately for the live menu page — keep the two in sync.
- **Breads** are the fixed keys `shami` / `baladi` / `fransawi` (`BREADS` dict), mirrored by the three `price_*` columns on `items` and the three-price tuples in `menu.json`. Adding a bread type touches all three places plus the `CASE` in `order_lines()`.
- **Item photos/descriptions** come from `menu_details.json` (loaded once at import as `ITEM_DETAILS`, exposed to templates as `details`), keyed by the Arabic item name; photos live in `static/items/`. The photos have an arrow button baked into the bottom-right corner — `.dish-badge` (price badge) is positioned to cover it and `.order-thumb img` zooms to crop it, so don't move either without checking.
- **Flash messages** render as fixed bottom toasts that stay until dismissed (the admin's reset-password message must remain readable).
- Menu content and restaurant-facing labels are Arabic (e.g. the `extras` filter only spells out deviations like "بدون طحينة"); UI chrome and flash messages are English.

## Deployment

- Production runs on the lab Docker host as container `food-food-1` behind nginx at `food.vcf.lab` (`deploy/nginx.conf`), with certs in `deploy/certs/` (gitignored).
- `docker-compose.prod.yml` pins a published image tag (`aidyland911/hamada-food:<N>.0`). Shipping a code or menu change means building/pushing a new tag and bumping that pin — the commit history follows this pattern.
- `ADMIN_USERNAME`/`ADMIN_PASSWORD` only take effect on the very first start (admin is created once, never updated). Account fixes afterwards are done directly against `/data/food.db` inside the container using Werkzeug's `generate_password_hash`.
- All state is the single file `data/food.db` (gitignored).

## Local databases

None of these is the live production DB (that lives on the lab host), and all of them carry prices older than the current `menu.json`:

- `./data/food.db` — local Docker test data (fake users), owned by root because the container wrote it.
- `~/dev/food_database/data/food.db` — a snapshot of the production DB from 2026-06-17 with real team users and two closed rounds; `food.db.bak` beside it is the same data just before round 2 was closed. Useful for testing migrations or price updates against realistic data — work on a copy.
