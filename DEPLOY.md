# Deploying to company infra (with MS Enterprise CA)

Target: a Linux server (or VM) in the lab with Docker + the compose plugin,
reachable by the team, with HTTPS via a certificate from your Microsoft
Enterprise CA (AD CS). Domain-joined machines already trust your Enterprise
CA root (it's published through AD/GPO), so browsers will show the padlock
with no warnings.

Pick an internal DNS name first — examples below use `food.example.corp`.

## 1. DNS record

In **AD DNS** (DNS Manager on a domain controller, or ask your AD admin):
add an **A record** `food` in your zone pointing to the lab server's IP.
Verify from any client: `nslookup food.example.corp`.

## 2. Get the app onto the server

```bash
git clone <this-repo> food && cd food
cp .env.example .env
openssl rand -hex 32        # paste as SECRET_KEY in .env
# set ADMIN_PASSWORD in .env too (used on first start only)
```

Edit `deploy/nginx.conf` and replace `food.example.corp` with your real name.

## 3. Private key + CSR (on the server)

```bash
cat > /tmp/san.cnf <<'EOF'
[req]
distinguished_name = dn
req_extensions = ext
prompt = no
[dn]
CN = food.example.corp
[ext]
subjectAltName = DNS:food.example.corp
EOF

openssl req -new -newkey rsa:2048 -nodes \
  -keyout deploy/certs/privkey.pem -out /tmp/food.csr -config /tmp/san.cnf
chmod 600 deploy/certs/privkey.pem
```

The **SAN** entry matters — browsers ignore CN-only certs.

## 4. Submit the CSR to the Enterprise CA

Either way works; do it from any domain-joined Windows machine.

**Option A — web enrollment (if `certsrv` is installed):**
1. Browse to `https://<ca-server>/certsrv` → *Request a certificate* →
   *Advanced certificate request*.
2. Paste the contents of `food.csr`, choose the **Web Server** template,
   submit.
3. Download the certificate as **Base 64 encoded** → `food.cer`.

**Option B — certreq (always available):**
```bat
certreq -submit -attrib "CertificateTemplate:WebServer" food.csr food.cer
```

If the request is *pending*, a CA admin must issue it in the Certification
Authority MMC (Pending Requests → All Tasks → Issue), then:
`certreq -retrieve <RequestId> food.cer`.

> If your CA refuses the Web Server template, ask your PKI admin to grant
> your account **Enroll** permission on it (template Security tab), or to
> duplicate it into a template you may enroll.

## 5. Build the chain file

Get the CA certificate(s) in Base64/PEM. On a domain machine:
`certutil -ca.cert ca.cer` (repeat per tier if you have root + issuing CA),
or download from `certsrv` → *Download a CA certificate, certificate chain*.

Convert DER → PEM if needed and assemble (server cert first, then issuing
CA, then root):

```bash
openssl x509 -inform der -in food.cer -out food.pem 2>/dev/null || cp food.cer food.pem
openssl x509 -inform der -in ca.cer -out ca.pem   2>/dev/null || cp ca.cer ca.pem
cat food.pem ca.pem > deploy/certs/fullchain.pem
```

## 6. Start

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

Check: `https://food.example.corp` from a domain-joined machine — padlock,
no warning. Port 80 redirects to HTTPS automatically.

Verify the chain from the server itself:
```bash
openssl s_client -connect localhost:443 -servername food.example.corp </dev/null | head -5
```

## 7. Migrating the existing data from your test machine

Users, menu, and order history live in `./data/food.db`. Stop the app on
your machine (`docker compose down`), copy the `data/` folder to the server
next to the repo, and start. Done — same accounts, same history. (Skip this
if you want a clean start; first boot creates a fresh DB with the admin from
`.env`.)

## 8. Ongoing

- **Cert renewal:** Web Server template certs usually last 1–2 years. Watch
  the expiry (`openssl x509 -enddate -noout -in deploy/certs/fullchain.pem`);
  repeat steps 3–5 and `docker compose -f docker-compose.prod.yml restart nginx`.
- **Backups:** the whole state is one file — cron-copy `data/food.db`
  somewhere safe.
- **Non-domain devices** (personal phones on lab Wi-Fi) won't trust the
  Enterprise root; install the root cert on them once, or accept the warning.
