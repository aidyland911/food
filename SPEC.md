# Hamada Sheraton Order App — Functional Spec

Single Flask app (`app.py`) + SQLite, server-rendered Jinja templates, no
JavaScript framework. Everything below is the intended behavior; the code is
the implementation of this spec.

## Purpose

Coordinate one team food order ("round") at a time from the Hamada Sheraton
restaurant: members build their own sandwich orders, the admin (Aidy) reads
one combined summary for the phone call and collects the right amount from
each person.

## Roles

- **Member** — self-registers, orders in the open round, sets a personal tip,
  views own history, reorders from history, changes own password.
- **Admin** — everything a member can do, plus: open/close rounds, set the
  delivery fee, view live and past round summaries, reset any member's
  password. Created once on first
  startup from `ADMIN_USERNAME`/`ADMIN_PASSWORD` (defaults `aidy`/`aidy123`);
  never re-created or updated after that.

## Core concepts

### Round
- At most **one round is open at a time** (opening while one is open is
  rejected).
- Has a `delivery_fee` (≥ 0), editable while open.
- Ordering is only possible while the round is open; closing freezes all
  orders in it.

### Order
- One order per user per round (`UNIQUE (round_id, user_id)`), created
  implicitly on the first action that needs it.
- Consists of lines: item + bread + quantity + salad/tahini flags.
- Lines with identical item/bread/salad/tahini are **merged** (quantity is
  summed) rather than duplicated.
- Salad and tahini default to **on**; the UI and summaries only call out
  deviations, in Arabic (e.g. "بدون طحينة").

### Items and prices
- Menu items belong to categories; every item has **three prices**, one per
  bread type: شامى (`shami`), بلدى (`baladi`), فرنساوى (`fransawi`).
- **Prices are never stored on order lines.** Every total — live cart,
  admin summary, history, reorder — is computed by joining the current
  `items` table. Consequences:
  - A price change applies immediately everywhere, including past-round
    displays ("I paid" in history reflects *current* prices, not the amount
    actually paid at the time).
  - Reordering an old round is automatically charged at today's prices.
- Menu seeding from `menu.json` on startup is **additive only**: new
  categories/items are inserted; existing rows (including prices) are left
  untouched. Editing a price in `menu.json` alone does nothing for an
  existing item — the DB row must be updated (or the DB recreated).

### Photos and descriptions
- `menu_details.json` maps an item's Arabic name (the same key as in
  `menu.json`) to an English name, a short description and a photo under
  `static/items/`. It is display-only: the menu page shows a photo card per
  item, and items without an entry get a placeholder.
- The photos have a round arrow button baked into their bottom-right corner
  (they were cropped from screenshots). The menu card covers it with the
  round "from <lowest price>" badge, and the order thumbnails zoom into the
  top-left to crop it out — keep that in mind when changing either.

### Tip
- Per user per round: yes/no plus an amount.
- "Yes" requires an amount **greater than 0**; any decimal amount is allowed
  (UI granularity 0.01 EGP, e.g. 1.70 or 0.37). "No" resets the stored
  amount to 0.
- The tip is added only to that member's own total, never split.

### Reorder
- From **My orders**, a member can reorder any past round they participated
  in, while a round is open.
- Copies that round's lines (item, bread, qty, salad/tahini) into their
  order in the current round, using the standard merge rule — reordering
  twice doubles quantities.
- Uses current prices; the confirmation message states the new food total.
- Rejected with a message when no round is open or the past round has no
  lines for that user.

### Password reset
- Passwords are stored only as hashes, so nobody (including the admin) can
  view one. A member who forgot theirs asks the admin, who picks the member
  on the admin page and sets a new password — or leaves the field blank to
  get a random 8-character one. The new password is shown once to the admin
  in the confirmation message.
- The account, and therefore its order history, is unchanged.

### Change password
- Any logged-in user can change their own password at `/password` by
  entering the current one and the new one twice. A wrong current password
  or mismatched new passwords are rejected with a message.

## The money math

For a round with delivery fee `F` and `N` participants (users with at least
one order line — a tip alone does not count):

```
share        = F / N
person_total = person_food_total + share + person_tip
grand_total  = Σ food + F + Σ tips
```

The live menu page shows the share "so far" (it changes as people join).

## Screens

| Screen | Route | Who |
|---|---|---|
| Register / Login / Logout | `/register`, `/login`, `/logout` | anyone |
| Menu + my order + tip | `/` | logged in |
| My previous orders (+ reorder) | `/history` | logged in |
| Change my password | `/password` | logged in |
| Admin: live summary, open/close, fee, past rounds | `/admin` | admin |
| Past round detail | `/admin/round/<id>` | admin |

Mutations are plain POST forms: `/order/add`, `/order/remove/<line_id>`,
`/order/reorder/<round_id>`, `/order/tip`, `/admin/round/open`,
`/admin/round/fee`, `/admin/round/close`, `/admin/user/password`.

- **History** lists only *closed* rounds where the user had at least one
  line.
- **Admin summary** shows per-person breakdowns plus aggregate sandwich
  counts grouped by (item, bread, salad, tahini) and per-bread totals — the
  numbers needed to phone the restaurant.

## Data model (SQLite)

```
users(id, username UNIQUE NOCASE, password_hash, is_admin)
categories(id, name_ar, sort)
items(id, category_id → categories, name_ar,
      price_shami, price_baladi, price_fransawi)
rounds(id, status 'open'|'closed', delivery_fee, created_at, closed_at)
orders(id, round_id → rounds, user_id → users,
       tip 0|1, tip_amount, created_at, UNIQUE(round_id, user_id))
order_items(id, order_id → orders ON DELETE CASCADE, item_id → items,
            bread 'shami'|'baladi'|'fransawi', qty, salad 0|1, tahini 0|1)
```

- Usernames are case-insensitively unique.
- Passwords are stored as Werkzeug hashes.
- On startup, missing `salad`/`tahini` columns are added to `order_items`
  (migration for pre-extras databases).

## Non-goals / known limitations

- No historical price snapshots (see "Items and prices" above).
- No editing of quantities in place — remove the line and re-add.
- No item deletion or price editing UI; the menu is managed via `menu.json`
  + DB.
- No concurrency guarantees beyond SQLite's; intended for one small team.
- Timestamps are naive local server time.
