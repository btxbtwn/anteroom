"""Shared, durable operator mail budget and outbox. No seller operation runs here."""
import re
import time
import uuid
from datetime import datetime
from typing import Any

from aiosmtplib.errors import SMTPResponseException

from anteroom.repositories.error_email import LIFECYCLE_ACTIVE, report_release

DAY = 86400
ERROR_DIGEST_INTERVAL = 3600
WARNING_DIGEST_INTERVAL = 86400
HOURLY_CAP = 60
MAX_GROUPS_PER_EMAIL = 20
MAX_PENDING_GROUPS = 500
MAX_RETRY_DELAY = 3600
_INVALID_POSTGRES_TEXT = re.compile(r'[\x00\ud800-\udfff]')


def _postgres_safe(value: Any) -> Any:
    """Postgres text and JSONB reject NUL characters and unpaired surrogates."""
    if isinstance(value, str):
        return _INVALID_POSTGRES_TEXT.sub('\ufffd', value)
    if isinstance(value, dict):
        return {_postgres_safe(key): _postgres_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_postgres_safe(item) for item in value]
    return value


def _release_order(release):
    if not release:
        return None
    tag = release.rsplit('/', 1)[-1].rsplit(':', 1)[-1]
    # App version and the hosted workflow's vVERSION-SHA image labels.
    if version := re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-[a-f\d]{8})?', tag):
        return 'version', tuple(map(int, version.groups()))
    if deploy := re.fullmatch(r'deployment-([0-7][0-9A-HJKMNP-TV-Z]{25})', tag):
        return 'fly', tuple(map(ord, deploy[1]))
    return None


def _is_newer_release(incoming, resolved):
    current, previous = _release_order(incoming), _release_order(resolved)
    return bool(current and previous and current[0] == previous[0] and current[1] > previous[1])


def _warning_deadline(state, active):
    hourly = state.get('digest_after')
    if not active:
        return hourly
    if 'warning_digest_after' in state:
        return state['warning_digest_after']
    # The previous gate was one hour after the last digest or first queued warning.
    return hourly + WARNING_DIGEST_INTERVAL - 3600 if hourly is not None else None


def classify_failure(exc):
    """``(failure, last_error, pause seconds)`` for a refused or failed SMTP attempt.

    SMTP may include addresses and server identifiers. Keep a safe,
    actionable classification instead of retaining the raw response.
    """
    code = exc.code if isinstance(exc, SMTPResponseException) else None
    response = exc.message.lower() if isinstance(exc, SMTPResponseException) else ''
    quota = 'daily' in response and ('limit exceeded' in response or 'quota exceeded' in response)
    return ('quota_paused' if quota else 'delivery_failed',
            f'SMTP {code}: {"relay quota exceeded" if quota else "relay rejected delivery"}' if code else type(exc).__name__,
            DAY if quota else (MAX_RETRY_DELAY if code and code >= 500 else 300))


def reserve_direct_send(store, daily_limit: int, clock=time.time, *, keep_error_reserve: bool = True) -> bool:
    """Charge one message sent outside the outbox (seller feedback, a database alert) to the shared budget.

    Refused while the relay is paused or another sender holds the lease, and,
    with ``keep_error_reserve``, whenever it would cut into the quarter of the
    daily budget kept for errors. An operator alert is an error and may use it.
    The reservation commits before any network activity.
    """
    daily_limit = max(0, daily_limit)
    if not daily_limit:
        return False
    with store.transaction() as transaction:
        state = transaction.state
        now = clock()
        attempts = state['attempts'] = [t for t in state.get('attempts', []) if t > now - DAY]
        reserve = max(1, daily_limit // 4) if keep_error_reserve else 0
        if (len(attempts) >= daily_limit - reserve
                or sum(t > now - 3600 for t in attempts) >= HOURLY_CAP
                or state.get('paused_until', 0) > now or state.get('lease_until', 0) > now):
            return False
        attempts.append(now)
        return True


def record_direct_failure(store, exc, clock=time.time) -> None:
    """A relay refusal of a direct message pauses every sender, as one from the outbox does."""
    failure, last_error, delay = classify_failure(exc)
    with store.transaction() as transaction:
        state = transaction.state
        state['failure'], state['last_error'] = failure, last_error
        state['paused_until'] = max(state.get('paused_until', 0), clock() + delay)
        state['last_failure_at'] = clock()


class EmailDelivery:
    def __init__(
        self,
        store,
        send,
        *,
        daily_limit: int,
        clock=time.time,
        enable_lifecycle: bool = True,
        max_pending_groups: int = MAX_PENDING_GROUPS,
    ):
        self.store, self.send, self.clock = store, send, clock
        self.daily_limit = max(0, daily_limit)
        self.enable_lifecycle = enable_lifecycle
        self.max_pending_groups = max(1, int(max_pending_groups))

    def _lifecycle_active(self, transaction):
        # The first opted-in sender activates the policy for every upgraded
        # sender under the same lock used to reserve mail.
        if self.enable_lifecycle and not transaction.state.get(LIFECYCLE_ACTIVE):
            transaction.seed_pending_issues()
            transaction.state[LIFECYCLE_ACTIVE] = True
        return transaction.state.get(LIFECYCLE_ACTIVE, False)

    def add(self, report):
        self.add_many([report])

    def add_many(self, reports):
        items = [_postgres_safe(report) for report in reports if report is not None]
        if not items:
            return
        with self.store.transaction() as transaction:
            state = transaction.state
            active = self._lifecycle_active(transaction)
            now = self.clock()
            pending_count: int | None = None
            for report in items:
                fingerprint = report['severity'] + ':' + report['fingerprint']
                issue = transaction.get_issue(fingerprint)
                group, last = (issue.payload, issue.last_sent) if issue else (None, None)
                occurred = datetime.fromisoformat(report['occurred_at']).timestamp()
                release = report_release(report)
                warning = report['severity'] == 'WARNING'
                updates = dict(occurrence_count=max(issue.occurrence_count if issue else 0, group['count'] if group else 0) + 1,
                               first_seen=min(issue.first_seen, occurred) if issue and issue.first_seen is not None else occurred,
                               last_seen=max(issue.last_seen, occurred) if issue and issue.last_seen is not None else occurred,
                               headline=report['title'], last_release=release) if active else {}
                regression = active and issue and issue.status == 'resolved' and _is_newer_release(release, issue.resolved_release)
                expired = (active and issue and issue.status == 'muted'
                           and issue.muted_until is not None and now >= issue.muted_until)
                if issue and issue.status != 'open' and not (regression or expired):
                    transaction.record_issue(fingerprint, **updates)
                    continue
                if regression or expired:
                    updates.update(status='open', resolved_at=None, muted_until=None)
                if group:
                    group['count'] += 1
                    group['report'] = report
                else:
                    if pending_count is None:
                        pending_count = int(transaction.pending_group_count())
                    if pending_count >= self.max_pending_groups:
                        reserved = state.get('lease_keys') if state.get('lease_until', 0) > now else None
                        evicted = 0 if warning else int(transaction.evict_oldest_warning(exclude=reserved))
                        if evicted > 0:
                            state['overflow_dropped'] = int(state.get('overflow_dropped', 0) or 0) + evicted
                            pending_count -= 1
                        else:
                            state['overflow_dropped'] = int(state.get('overflow_dropped', 0) or 0) + 1
                            if active and issue is not None:
                                transaction.record_issue(fingerprint, **updates)
                            continue
                    if warning:
                        deadline = _warning_deadline(state, active)
                        if deadline is None:
                            gate = 'warning_digest_after' if active else 'digest_after'
                            interval = WARNING_DIGEST_INTERVAL if active else 3600
                            deadline = state[gate] = now + interval
                        due = max(now, deadline)
                    else:
                        due = max(now, last + ERROR_DIGEST_INTERVAL) if last is not None else now
                    group = {'report': report, 'count': 1, 'first': report['occurred_at'], 'due': due}
                    pending_count += 1
                if not warning and (regression or expired):
                    # An older SMTP reservation may still be completing.
                    group['priority_token'] = uuid.uuid4().hex
                    group['due'], last = now, None
                    if regression:
                        group['regressed_from'] = issue.resolved_release
                        group['regression_report'] = report
                transaction.put_group(fingerprint, group, last, **updates)

    def tick(self):
        if not self.daily_limit:
            # An operator can use triage even when SMTP has no configured budget.
            if self.enable_lifecycle:
                with self.store.transaction() as transaction:
                    self._lifecycle_active(transaction)
                self.enable_lifecycle = False
            return
        from anteroom.services.error_alerts import render_email

        with self.store.transaction() as transaction:
            state = transaction.state
            active = self._lifecycle_active(transaction)
            now = self.clock()
            warning_interval = WARNING_DIGEST_INTERVAL if active else 3600
            attempts = state['attempts'] = [t for t in state.get('attempts', []) if t > now - DAY]
            transaction.expire_sent(now - DAY)
            if (len(attempts) >= self.daily_limit
                    or sum(t > now - 3600 for t in attempts) >= HOURLY_CAP
                    or state.get('paused_until', 0) > now or state.get('lease_until', 0) > now):
                return
            # A first alert never waits behind already-notified fingerprints.
            errors = transaction.due_groups(now, warning=False, limit=MAX_GROUPS_PER_EMAIL,
                                            first_alert=True)
            # Reserve at least one slot for errors whenever the budget permits.
            reserve = max(1, self.daily_limit // 4)
            selected = errors
            if not errors and len(attempts) < self.daily_limit - reserve and (_warning_deadline(state, active) or 0) <= now:
                selected = transaction.due_groups(now, warning=True, limit=MAX_GROUPS_PER_EMAIL)
            repeats = not selected and state.get('error_digest_after', 0) <= now
            if repeats:
                errors = transaction.due_groups(now, warning=False, limit=MAX_GROUPS_PER_EMAIL,
                                                sent_before=now - ERROR_DIGEST_INTERVAL)
                selected = errors
            if not selected:
                return
            # Reserve before SMTP, including failures and uncertain crash outcomes.
            # No network call holds this transaction or a database connection.
            now = self.clock()
            attempts.append(now)
            token = uuid.uuid4().hex
            state['lease'] = token
            state['lease_until'] = now + 120
            state['lease_keys'] = list(selected)
            overflow = int(state.get('overflow_dropped', 0) or 0)
        count = sum(group['count'] for group in selected.values())
        sections = []
        for group in selected.values():
            report = group.get('regression_report', group['report'])
            title, section = render_email(_postgres_safe(report), group['count'] - 1, group['first'])
            if previous := group.get('regressed_from'):
                section = f'Previously resolved on release {previous}\n\n{section}'
                if report != group['report']:
                    _, latest = render_email(_postgres_safe(group['report']), 0, None)
                    section += f'\n\nLatest occurrence\n-----------------\n{latest}'
            sections.append((title, section))
        subject = sections[0][0] if len(selected) == 1 and errors else f'[Anteroom hosted] {"Errors" if errors else "Warnings"}: {count} occurrences'
        if any(group.get('regressed_from') for group in selected.values()):
            subject = '[Regression] ' + subject
        header = f'{count} occurrences in {len(selected)} groups\n\n'
        if overflow > 0:
            header += (
                f'Note: {overflow} additional alerts were omitted while the outbox '
                f'was at its {self.max_pending_groups}-group limit.\n\n'
            )
        body = header + '\n\n'.join(body for _, body in sections)
        failure = None
        try:
            self.send(subject, body)
        except Exception as exc:
            failure = classify_failure(exc)
        with self.store.transaction() as transaction:
            state = transaction.state
            if state.get('lease') != token:
                return
            state['lease_until'] = 0
            state.pop('lease_keys', None)
            if failure:
                state['failure'], state['last_error'], delay = failure
                retry_delay = min(MAX_RETRY_DELAY, max(delay, 2 * state.get('retry_delay', 0)))
                state['retry_delay'] = retry_delay
                state['paused_until'] = self.clock() + max(delay, retry_delay)
                state['last_failure_at'] = self.clock()
            else:
                state.pop('retry_delay', None)
                state['last_success_at'] = self.clock()
                state['paused_until'] = 0
                state['last_error'] = None
                state['failure'] = None
                if overflow > 0:
                    remaining_overflow = max(0, int(state.get('overflow_dropped', 0) or 0) - overflow)
                    if remaining_overflow:
                        state['overflow_dropped'] = remaining_overflow
                    else:
                        state.pop('overflow_dropped', None)
                for key, sent in selected.items():
                    current, last = transaction.get_group(key)
                    if current is None:
                        continue
                    remaining = max(0, current['count'] - sent['count'])
                    superseded = current.get('priority_token') != sent.get('priority_token')
                    if remaining:
                        current['count'] = remaining
                        if not superseded:
                            current.pop('regressed_from', None)
                            current.pop('regression_report', None)
                            current.pop('priority_token', None)
                            interval = warning_interval if not errors else ERROR_DIGEST_INTERVAL
                            current['due'] = now + interval
                    transaction.put_group(key, current if remaining else None, last if superseded else now)
                if not errors:
                    gate = 'warning_digest_after' if active else 'digest_after'
                    state[gate] = now + warning_interval
                elif repeats:
                    state['error_digest_after'] = now + ERROR_DIGEST_INTERVAL
                else:
                    # New fingerprints must not keep sliding the repeat deadline.
                    state.setdefault('error_digest_after', now + ERROR_DIGEST_INTERVAL)

    def status(self):
        state, pending = self.store.summary()
        now = self.clock()
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
                'pending': pending,
                'overflow_dropped': int(state.get('overflow_dropped', 0) or 0),
                'resume_at': resume, 'last_error': state.get('last_error'),
                'last_success_at': state.get('last_success_at')}
