# Deploying cafe-flow

One always-on process with a persistent disk. Not serverless: SSE needs
long-lived connections and SQLite needs a real filesystem.

**Every command needs `-c deploy/fly.toml`.** The config does not live at the
repository root, so without it fly looks for `./fly.toml`, does not find one, and
reports a missing app name rather than a missing config -- which sends you
looking in the wrong place. `-a cafe-flow-demo` works too.

## First deploy

    fly launch --no-deploy -c deploy/fly.toml
    fly volumes create cafe_data --size 1 --region ord -c deploy/fly.toml
    fly scale count 1 -c deploy/fly.toml    # the ceiling; fly.toml sets the floor
    fly deploy -c deploy/fly.toml

Then check the three views: `/` student, `/bar` barista, `/pickup` display.

## Staff

`/bar` and every write route need a login. Customers need none and have none.

    fly secrets set -c deploy/fly.toml \
      CAFE_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
    fly ssh console -c deploy/fly.toml -C "python -m app.create_staff <name>"

**Set the key before anyone logs in.** Without it the app generates one at boot,
says so in the log, and every session ends when the process does -- survivable
for a demo, useless for a shift. Changing it later logs everybody out, which is
the correct behaviour and still a surprise if you did not mean it.

There is no registration endpoint and no password reset. Accounts are made by
somebody with a shell, which for one bar is the right amount of ceremony.

## Backups

Litestream replicates `/data/cafe.db` continuously. Set the credentials as
secrets — without `LITESTREAM_BUCKET` the container runs unreplicated and says
so at boot, which is fine for `dev` and `demo` and not fine for `pilot`.

    fly secrets set -c deploy/fly.toml \
      LITESTREAM_BUCKET=cafe-flow-backups \
      LITESTREAM_ENDPOINT=https://<account>.r2.cloudflarestorage.com \
      LITESTREAM_ACCESS_KEY_ID=... \
      LITESTREAM_SECRET_ACCESS_KEY=...

**Verify the restore before the pilot, not after.** A backup nobody has
restored is not a backup:

    fly volumes create cafe_restore_test --size 1 -c deploy/fly.toml
    # attach it to a one-off machine, then inside it:
    litestream restore -config /etc/litestream.yml /data/restore-check.db
    sqlite3 /data/restore-check.db "select count(*) from order_events"

## Environments

Three apps, three volumes, three databases. Never one app with a flag.

| Env | `CAFE_ENV` | Database | Simulated orders |
|---|---|---|---|
| dev | `dev` | local file | allowed |
| demo | `demo` | Fly volume | allowed, seeded by the M9 replay client |
| pilot | `pilot` | Fly volume | **rejected at the API with 403** |

In `pilot`, `POST /orders` with `X-Simulated-Order` returns 403 and the barista
view filters simulated orders server-side. A simulated order appearing on the
bar during a real rush destroys trust in the tool permanently.

## Parameters

`params/` lives on the volume, not in the image. On first boot the image seeds
every calibrated layer it ships -- `base.yaml`, `observed.yaml`,
`hybrid_arrivals.yaml` and `forecast.yaml` -- and never touches them again, so a
fresh machine comes up on the measured cafe rather than the assumed one. It used
to seed `base.yaml` alone, which meant a first deploy served the invented class
timetable and the wrong opening hours while every calibrated file sat in the
image beside it.

Recalibrating afterwards is a file drop and a restart:

    fly ssh sftp shell -c deploy/fly.toml
    put params/observed.yaml /data/params/observed.yaml
    fly apps restart -c deploy/fly.toml

The entrypoint overlays whatever it finds and logs each one, so the boot line
says which configuration the machine is actually serving. An explicitly set
`CAFE_PARAMS` wins over all of it, which is how you reproduce a scenario on a
running machine.

## SSE, and what will bite

* **Proxy buffering.** The stream sends `X-Accel-Buffering: no`. A buffering
  proxy makes SSE appear to work locally and silently fail in production.
* **Idle timeouts.** The heartbeat is 15s (`CAFE_SSE_HEARTBEAT_S`). Keep it
  below whatever idle timeout sits in front of the app.
* **Reconnects.** Clients resume with `Last-Event-ID` *and* refetch `/queue`.
  Resuming the stream alone leaves a client that missed events showing a queue
  that is quietly wrong. Cafe wifi drops; this is not hypothetical.

## Rollback

    fly releases -c deploy/fly.toml
    fly deploy --image <previous-image> -c deploy/fly.toml

Under a minute, which is the M11 requirement. Practise it before the pilot day.
