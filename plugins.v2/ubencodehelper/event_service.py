from .notification_service import NotificationService


class EventService:
    MAX_PAGES = 5
    PAGE_SIZE = 200

    def __init__(self, plugin):
        self.plugin = plugin

    def sync(self, client, token: str, config: dict) -> dict:
        state = dict(self.plugin.get_data("event_state") or {})
        cursor = max(0, int(state.get("cursor") or 0))
        seen = [str(value) for value in list(state.get("seen") or []) if value]
        seen_set = set(seen)
        recent = [dict(item) for item in list(self.plugin.get_data("recent_events") or []) if isinstance(item, dict)]
        processed = 0
        sent = 0
        notifier = NotificationService(self.plugin)

        for _page in range(self.MAX_PAGES):
            response = client.events(token, after_id=cursor, limit=self.PAGE_SIZE)
            events = list(response.get("events") or [])
            if not events:
                break
            for event in events:
                event_id = str(event.get("event_id") or "")
                event_row_id = int(event.get("id") or 0)
                if event_row_id <= cursor:
                    continue
                notified = False
                if event_id and event_id not in seen_set and notifier.should_notify(event, config):
                    notifier.send_event(event)
                    sent += 1
                    notified = True
                if not event_id or event_id not in seen_set:
                    recent.insert(0, notifier.timeline_item(event, notified))
                    processed += 1
                cursor = event_row_id
                if event_id:
                    seen.append(event_id)
                    seen_set.add(event_id)
                seen = seen[-500:]
                seen_set = set(seen)
                self.plugin.save_data("event_state", {"cursor": cursor, "seen": seen})
                self.plugin.save_data("recent_events", recent[:80])
            if not response.get("has_more"):
                break
        return {"ok": True, "processed": processed, "sent": sent, "cursor": cursor}
