from rcsscrapper.models import Roommate, Receipt
from rcsscrapper.utils import ensure_roommates


def splitflow_context(request):
    """
    Supplies global navigation context to all templates:
    - nav_active_roommates: list of active roommates (Het, Ruchit, Tirth, Maurya)
    - nav_current_roommate: currently authenticated roommate profile (or None)
    - nav_is_admin: True if user is administrator / superuser / staff
    - nav_pending_receipts_count: number of unprocessed Superstore receipts (admin only)
    """
    ensure_roommates()
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
        'nav_active_roommates': active_roommates,
        'nav_current_roommate': current_rm,
        'nav_is_admin': is_admin,
        'nav_pending_receipts_count': pending_count,
    }
