# Multi-gateway deployment

Hermes supports multiple gateway processes running concurrently — one per profile
(default, writer, admin, coder, researcher). Each gateway opens its own connection
to platform APIs and delivers messages for its profile's subscribers.

## Dispatcher vs notifier ownership

Kanban now separates two gateway responsibilities:

- **Dispatcher ownership** (`kanban.dispatch_in_gateway`) — whether this gateway
  spawns worker processes for ready tasks.
- **Notifier ownership** (`kanban.notify_in_gateway`) — whether this gateway polls
  Kanban subscriptions and sends task terminal events (`completed`, `blocked`,
  `crashed`, etc.) back to messaging platforms.

This split supports the safe Hermes-VM posture: keep Telegram responsive in the
gateway, but move worker spawning to an external `hermes kanban daemon` service.

## Configuration patterns

### Default single-gateway setup

No change is needed:

```yaml
kanban:
  dispatch_in_gateway: true
  notify_in_gateway: true
```

### External dispatcher / worker host

On the Telegram gateway host:

```yaml
kanban:
  dispatch_in_gateway: false
  notify_in_gateway: true
```

Then run the dispatcher elsewhere against the same board path/DB:

```bash
hermes kanban daemon
```

`hermes kanban daemon` runs without `--force` only when
`kanban.dispatch_in_gateway=false`; otherwise it refuses by default to prevent two
dispatchers racing for claims. `--force` remains as an explicit legacy escape
hatch.

Runtime overrides:

```bash
HERMES_KANBAN_DISPATCH_IN_GATEWAY=false  # disable gateway worker spawning
HERMES_KANBAN_NOTIFY_IN_GATEWAY=false    # disable gateway kanban notifications
```

### Secondary profile gateways

For extra profile gateways that should neither spawn workers nor deliver Kanban
subscription events:

```yaml
kanban:
  dispatch_in_gateway: false
  notify_in_gateway: false
```

## What each gateway does

| Gateway role | dispatch_in_gateway | notify_in_gateway | Opens per-board DBs? | Runs dispatcher? | Sends task notifications? |
|---|---:|---:|---|---|---|
| default all-in-one | true | true | yes | yes | yes |
| Telegram + external dispatcher | false | true | notifier only | no | yes |
| secondary non-owner gateway | false | false | no | no | no |
