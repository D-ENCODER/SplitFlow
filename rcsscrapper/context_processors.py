from rcsscrapper.models import Roommate, Receipt


def splitflow_context(request):
    """
    Supplies global navigation context to all templates:
    - nav_active_roommates: list of the 4 active roommates (Het, Ruchit, Tirth, Maurya)
    - nav_current_roommate: currently authenticated roommate (or Het as default)
    - nav_pending_receipts_count: number of unprocessed Superstore receipts
    """
    active_roommates = list(Roommate.objects.filter(is_active=True).select_related('user'))
    
    current_rm = None
    if request.user.is_authenticated and hasattr(request.user, 'roommate') and request.user.roommate:
        current_rm = request.user.roommate
    else:
        current_rm = next((r for r in active_roommates if r.is_me), active_roommates[0] if active_roommates else None)

    pending_count = Receipt.objects.filter(is_archived=False, processed=False).count()

    return {
        'nav_active_roommates': active_roommates,
        'nav_current_roommate': current_rm,
        'nav_pending_receipts_count': pending_count,
    }
