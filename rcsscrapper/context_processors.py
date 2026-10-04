from rcsscrapper.models import Roommate, Receipt, HouseholdGroup
from rcsscrapper.utils import ensure_roommates, ensure_groups


def get_active_group(request):
    ensure_groups()
    group_id = request.session.get('active_group_id')
    if group_id:
        group = HouseholdGroup.objects.filter(id=group_id).first()
        if group:
            return group

    user_rm = getattr(request.user, 'roommate', None) if request.user.is_authenticated else None
    if user_rm and user_rm.groups.exists():
        group = user_rm.groups.first()
        request.session['active_group_id'] = group.id
        return group

    group = HouseholdGroup.objects.filter(name='41-27 Centennial').first() or HouseholdGroup.objects.first()
    if group:
        request.session['active_group_id'] = group.id
    return group


def splitflow_context(request):
    """
    Supplies global navigation context to all templates:
    - nav_active_group: currently active HouseholdGroup
    - nav_all_groups: list of groups accessible to the user
    - nav_active_roommates: list of active roommates in current group
    - nav_current_roommate: currently authenticated roommate profile (or None)
    - nav_is_admin: True if user is administrator / superuser / staff
    - nav_pending_receipts_count: number of unprocessed Superstore receipts (admin only)
    """
    ensure_roommates()
    ensure_groups()
    active_group = get_active_group(request)

    all_groups = list(HouseholdGroup.objects.all())

    if active_group:
        active_roommates = list(active_group.members.filter(is_active=True).select_related('user'))
        if not active_roommates:
            active_roommates = list(Roommate.objects.filter(is_active=True).select_related('user'))
    else:
        active_roommates = list(Roommate.objects.filter(is_active=True).select_related('user'))

    current_rm = None
    is_admin = False

    if request.user.is_authenticated:
        is_admin = bool(request.user.is_superuser or request.user.is_staff or request.user.username == 'admin')
        if hasattr(request.user, 'roommate') and request.user.roommate:
            current_rm = request.user.roommate
        else:
            current_rm = next((r for r in active_roommates if r.is_me), active_roommates[0] if active_roommates else None)

    pending_count = Receipt.objects.filter(is_archived=False, processed=False).count() if is_admin else 0

    return {
        'nav_active_group': active_group,
        'nav_all_groups': all_groups,
        'nav_active_roommates': active_roommates,
        'nav_current_roommate': current_rm,
        'nav_is_admin': is_admin,
        'nav_pending_receipts_count': pending_count,
    }
