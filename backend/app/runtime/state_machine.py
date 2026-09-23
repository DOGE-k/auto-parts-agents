from app.domain.models import OperationalState


ALLOWED_TRANSITIONS: dict[OperationalState, frozenset[OperationalState]] = {
    OperationalState.READY: frozenset({OperationalState.VALIDATING, OperationalState.CANCELLED}),
    OperationalState.VALIDATING: frozenset(
        {
            OperationalState.EXECUTING,
            OperationalState.AWAITING_APPROVAL,
            OperationalState.RETRY,
            OperationalState.EXCEPTION,
            OperationalState.WAITING_EVENT,
            OperationalState.CANCELLED,
        }
    ),
    OperationalState.EXECUTING: frozenset(
        {
            OperationalState.DONE,
            OperationalState.WAITING_EVENT,
            OperationalState.AWAITING_APPROVAL,
            OperationalState.RETRY,
            OperationalState.EXCEPTION,
            OperationalState.CANCELLED,
        }
    ),
    OperationalState.WAITING_EVENT: frozenset(
        {OperationalState.VALIDATING, OperationalState.CANCELLED, OperationalState.EXCEPTION}
    ),
    OperationalState.AWAITING_APPROVAL: frozenset(
        {
            OperationalState.EXECUTING,
            OperationalState.WAITING_EVENT,
            OperationalState.EXCEPTION,
            OperationalState.CANCELLED,
        }
    ),
    OperationalState.RETRY: frozenset(
        {OperationalState.EXECUTING, OperationalState.EXCEPTION, OperationalState.CANCELLED}
    ),
    OperationalState.EXCEPTION: frozenset(
        {OperationalState.VALIDATING, OperationalState.CANCELLED}
    ),
    OperationalState.DONE: frozenset(),
    OperationalState.CANCELLED: frozenset(),
}


def transition(current: OperationalState, target: OperationalState) -> OperationalState:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"不允许的操作状态迁移：{current.value} -> {target.value}")
    return target
