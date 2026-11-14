"""Shared, durable operator mail budget and outbox. No seller operation runs here."""
import smtplib
import time
import uuid

DAY = 86400
DIGEST_INTERVAL = 3600
REPEAT_WINDOW = 900
HOURLY_CAP = 60
MAX_GROUPS_PER_EMAIL = 20
# Every report rereads and rewrites the whole stored outbox, so its size is
# what each logged warning costs in database traffic. While nothing sends
# (no daily budget) groups never drain; keep only the newest ones.
MAX_STORED_GROUPS = 50


def _trim(groups: dict, *, keep: str) -> None:
    """Drop the oldest warnings first, then the oldest errors, down to MAX_STORED_GROUPS."""
    excess = len(groups) - MAX_STORED_GROUPS
    if excess <= 0:
        return
    oldest = sorted((key for key in groups if key != keep),
                    key=lambda key: (groups[key]['report']['severity'] != 'WARNING', str(groups[key]['first'])))
    for key in oldest[:excess]:
        del groups[key]


class EmailDelivery:
    def __init__(self, store, send, *, daily_limit: int, clock=time.time):
        self.store, self.send, self.clock = store, send, clock
        self.daily_limit = max(0, daily_limit)

    def add(self, report):
        now = self.clock()
        with self.store.transaction() as state:
            groups = state.setdefault('groups', {})
            fingerprint = report['severity'] + ':' + report['fingerprint']
            group = groups.get(fingerprint)
            if group:
                group['count'] += 1
                group['report'] = report
            else:
                last = state.get('last_sent', {}).get(fingerprint)
                warning = report['severity'] == 'WARNING'
                due = (max(now, state.setdefault('digest_after', now + DIGEST_INTERVAL)) if warning
                       else max(now, last + REPEAT_WINDOW) if last is not None else now)
                groups[fingerprint] = {'report': report, 'count': 1, 'first': report['occurred_at'], 'due': due}
            _trim(groups, keep=fingerprint)

    def tick(self):
        if not self.daily_limit:
            return
        from vendoo_studio.services.error_alerts import render_email

        now = self.clock()
        with self.store.transaction() as state:
            attempts = state['attempts'] = [t for t in state.get('attempts', []) if t > now - DAY]
            state['last_sent'] = {key: at for key, at in state.get('last_sent', {}).items() if at > now - DAY}
            if (len(attempts) >= self.daily_limit
                    or sum(t > now - 3600 for t in attempts) >= HOURLY_CAP
                    or state.get('paused_until', 0) > now or state.get('lease_until', 0) > now):
                return
            due = {key: group for key, group in state.get('groups', {}).items() if group['due'] <= now}
            errors = {key: group for key, group in due.items() if group['report']['severity'] != 'WARNING'}
            # Reserve at least one slot for errors whenever the budget permits.
            reserve = max(1, self.daily_limit // 4)
            warnings = {key: group for key, group in due.items() if group['report']['severity'] == 'WARNING'}
            selected = errors or (warnings if len(attempts) < self.daily_limit - reserve
                                   and state.get('digest_after', 0) <= now else {})
            selected = dict(list(selected.items())[:MAX_GROUPS_PER_EMAIL])
            if not selected:
                return
            # Reserve before SMTP, including failures and uncertain crash outcomes.
            # No network call holds this transaction or a database connection.
            attempts.append(now)
            token = uuid.uuid4().hex
            state['lease'] = token
            state['lease_until'] = now + 120
        count = sum(group['count'] for group in selected.values())
        sections = [render_email(group['report'], group['count'] - 1, group['first']) for group in selected.values()]
        subject = sections[0][0] if len(selected) == 1 and errors else f'[Anteroom hosted] {"Errors" if errors else "Warnings"}: {count} occurrences'
        body = f'{count} occurrences in {len(selected)} groups\n\n' + '\n\n'.join(body for _, body in sections)
        failure = None
        try:
            self.send(subject, body)
        except Exception as exc:
            # SMTP may include addresses and server identifiers. Keep a safe,
            # actionable classification instead of retaining the raw response.
            code = exc.smtp_code if isinstance(exc, smtplib.SMTPResponseException) else None
            response = exc.smtp_error if isinstance(exc, smtplib.SMTPResponseException) else ''
            response = (response.decode('utf-8', errors='replace') if isinstance(response, bytes) else response).lower()
            quota = 'daily' in response and ('limit exceeded' in response or 'quota exceeded' in response)
            failure = ('quota_paused' if quota else 'delivery_failed',
                       f'SMTP {code}: {"relay quota exceeded" if quota else "relay rejected delivery"}' if code else type(exc).__name__,
                       DAY if quota else (3600 if code and code >= 500 else 300))
        with self.store.transaction() as state:
            if state.get('lease') != token:
                return
            state['lease_until'] = 0
            if failure:
                state['failure'], state['last_error'], delay = failure
                state['paused_until'] = self.clock() + delay
                state['last_failure_at'] = self.clock()
            else:
                state['last_success_at'] = self.clock()
                state['paused_until'] = 0
                state['last_error'] = None
                state['failure'] = None
                groups = state['groups']
                for key, sent in selected.items():
                    state.setdefault('last_sent', {})[key] = now
                    remaining = groups[key]['count'] - sent['count']
                    if remaining:
                        groups[key]['count'] = remaining
                        groups[key]['due'] = now + (REPEAT_WINDOW if errors else DIGEST_INTERVAL)
                    else:
                        del groups[key]
                if not errors:
                    state['digest_after'] = now + DIGEST_INTERVAL

    def status(self):
        state, now = self.store.read(), self.clock()
        attempts = [at for at in state.get('attempts', []) if at > now - DAY]
        status = 'ready'
        resume = None
        if not self.daily_limit:
            status = 'needs_budget'
        elif state.get('paused_until', 0) > now:
            status, resume = state.get('failure', 'delivery_failed'), state['paused_until']
        elif len(attempts) >= self.daily_limit:
            status, resume = 'budget_exhausted', attempts[0] + DAY
        elif len(hour := [at for at in attempts if at > now - 3600]) >= HOURLY_CAP:
            status, resume = 'budget_exhausted', hour[0] + 3600
        return {'state': status, 'daily_limit': self.daily_limit, 'used': len(attempts),
                'pending': sum(group['count'] for group in state.get('groups', {}).values()),
                'resume_at': resume, 'last_error': state.get('last_error'),
                'last_success_at': state.get('last_success_at')}
