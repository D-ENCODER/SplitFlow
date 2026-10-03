import os
import subprocess
import tempfile
from decimal import Decimal
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from rcsscrapper.models import Receipt, ReceiptItem, Roommate, ItemShare
from rcsscrapper.utils import sync_receipts_folder, ensure_roommates
import json
import re
from bs4 import BeautifulSoup
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rcsscrapper.utils import INBOX_DIR




@csrf_exempt
def api_ingest_order(request):
    if request.method == "POST":
        data = json.loads(request.body)
        html_content = data.get("html", "")

        # Extract Order # from anywhere in the page first
        match = re.search(r"Order\s*#(\d+)", html_content)
        order_id = match.group(1) if match else "latest"
        filename = f"Superstore_Order_{order_id}.html"

        # If .single-column-wrapper exists, save only that clean block; otherwise save full HTML
        soup = BeautifulSoup(html_content, "html.parser")
        wrappers = soup.select("div.single-column-wrapper")
        if wrappers:
            # Only keep the wrapper if it actually has the cart items inside it
            combined = "\n".join(str(w) for w in wrappers)
            if "cart-entry-list__item" in combined:
                html_content = combined

        os.makedirs(INBOX_DIR, exist_ok=True)
        filepath = os.path.join(INBOX_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        # If this receipt was previously archived or had 0 items, unarchive/refresh it
        existing = Receipt.objects.filter(filename=filename).first()
        if existing:
            if existing.items.count() == 0:
                existing.delete()
            else:
                existing.is_archived = False
                existing.save()

        sync_receipts_folder()

        rec = Receipt.objects.filter(filename=filename).first()
        item_count = rec.items.count() if rec else 0

        response = JsonResponse({
            "status": "ok",
            "filename": filename,
            "items_found": item_count
        })
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    response = HttpResponse()
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def delete_receipts_and_files(queryset):
    for rec in queryset:
        filepath = os.path.join(INBOX_DIR, rec.filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
    queryset.delete()

def delete_receipt(request, receipt_id):
    receipt = get_object_or_404(Receipt, id=receipt_id)
    was_archived = receipt.is_archived
    delete_receipts_and_files(Receipt.objects.filter(id=receipt.id))
    if was_archived:
        return redirect(f"{reverse('receipt_list')}?archived=1")
    return redirect('receipt_list')

def latex_escape(text):
    conv = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
        '^': r'\^{}', '\\': r'\textbackslash{}',
    }
    return ''.join(conv.get(c, c) for c in str(text))

def receipt_list(request):
    sync_receipts_folder()
    show_archived = request.GET.get('archived') == '1'

    if request.method == 'POST':
        selected_ids = request.POST.getlist('receipt_ids')
        action = request.POST.get('action', 'process')
        if selected_ids:
            ids_str = ','.join(selected_ids)
            if action == 'archive':
                Receipt.objects.filter(id__in=selected_ids).update(is_archived=True)
                return redirect('receipt_list')
            elif action == 'unarchive':
                Receipt.objects.filter(id__in=selected_ids).update(is_archived=False)
                return redirect(f"{reverse('receipt_list')}?archived=1")
            elif action == 'delete':
                delete_receipts_and_files(Receipt.objects.filter(id__in=selected_ids))
                if show_archived:
                    return redirect(f"{reverse('receipt_list')}?archived=1")
                return redirect('receipt_list')
            elif action == 'reprocess':
                ReceiptItem.objects.filter(receipt_id__in=selected_ids).update(is_assigned=False)
                Receipt.objects.filter(id__in=selected_ids).update(processed=False)
                return redirect(f"{reverse('batch_process')}?ids={ids_str}")
            elif action == 'summary':
                return redirect(f"{reverse('batch_summary')}?ids={ids_str}")
            return redirect(f"{reverse('batch_process')}?ids={ids_str}")

    receipts = Receipt.objects.filter(is_archived=show_archived).order_by('-file_modified_at', '-created_at')
    archived_count = Receipt.objects.filter(is_archived=True).count()

    return render(request, 'splitter/receipt_list.html', {
        'receipts': receipts,
        'show_archived': show_archived,
        'archived_count': archived_count,
    })

def toggle_archive_receipt(request, receipt_id):
    receipt = get_object_or_404(Receipt, id=receipt_id)
    receipt.is_archived = not receipt.is_archived
    receipt.save()
    return redirect('receipt_list')

def batch_process(request):
    ensure_roommates()
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    roommates = list(Roommate.objects.all())
    all_items = list(
        ReceiptItem.objects.filter(receipt_id__in=receipt_ids)
        .select_related('receipt')
        .order_by('receipt__file_modified_at', 'id')
    )

    # #4: Allow jumping to a specific item (for "Previous Item" or editing from Summary Matrix)
    specific_item_id = request.GET.get('item_id')
    return_to = request.GET.get('from', '')

    item = None
    if specific_item_id and specific_item_id.isdigit():
        item = next((i for i in all_items if i.id == int(specific_item_id)), None)

    if not item:
        item = next((i for i in all_items if not i.is_assigned), None)

    if not item:
        Receipt.objects.filter(id__in=receipt_ids).update(processed=True)
        return redirect(f"{reverse('batch_summary')}?ids={ids_param}")

    if request.method == 'POST':
        ItemShare.objects.filter(item=item).delete()
        total_units = Decimal('0')

        for r in roommates:
            units = Decimal(request.POST.get(f'units_{r.id}', '0') or '0')
            if units > 0:
                ItemShare.objects.create(item=item, roommate=r, units=units)
                total_units += units

        if total_units > 0:
            item.is_assigned = True
            item.save()
            if not item.receipt.items.filter(is_assigned=False).exists():
                item.receipt.processed = True
                item.receipt.save()

            # If we clicked "Edit" from the Summary page, go right back to Summary after saving!
            if return_to == 'summary':
                return redirect(f"{reverse('batch_summary')}?ids={ids_param}")
            return redirect(f"{reverse('batch_process')}?ids={ids_param}")

    # Find current index & Previous Item ID for the "<- Previous Item" button (#4)
    current_idx = all_items.index(item)
    prev_item = all_items[current_idx - 1] if current_idx > 0 else None

    # #2: Smart Item Memory (or load existing shares if editing a previous item)
    initial_units = {r.id: Decimal('0') for r in roommates}
    smart_matched = False

    existing_shares = list(item.shares.all())
    if existing_shares:
        for s in existing_shares:
            initial_units[s.roommate_id] = s.units
    else:
        # Look up the most recent past item with the exact same name that was already split
        last_assigned = (
            ReceiptItem.objects.filter(name__iexact=item.name, is_assigned=True)
            .exclude(id=item.id)
            .order_by('-id')
            .first()
        )
        if last_assigned:
            for s in last_assigned.shares.all():
                initial_units[s.roommate_id] = s.units
            smart_matched = True

    roommate_rows = [
        {'roommate': r, 'initial_unit': f"{initial_units[r.id]:g}"}
        for r in roommates
    ]

    return render(request, 'splitter/assign_item.html', {
        'receipt': item.receipt,
        'item': item,
        'roommates': roommates,
        'roommate_rows': roommate_rows,
        'smart_matched': smart_matched,
        'prev_item': prev_item,
        'return_to': return_to,
        'ids_param': ids_param,
        'progress': {
            'current': current_idx + 1,
            'total': len(all_items),
            'receipt_count': len(receipt_ids),
        }
    })

def build_summary_data(receipt_ids):
    sync_receipts_folder()
    receipts = Receipt.objects.filter(id__in=receipt_ids).order_by('file_modified_at', 'id')
    roommates = list(Roommate.objects.all())
    num_roommates = len(roommates)

    overall_items = {r: Decimal('0.00') for r in roommates}
    overall_tax = {r: Decimal('0.00') for r in roommates}
    receipt_reports = []

    for receipt in receipts:
        # Keep exact unrounded running totals per receipt first
        r_items_exact = {r: Decimal('0.0000') for r in roommates}
        item_rows = []
        actual_items_sum = Decimal('0.00')

        for item in receipt.items.prefetch_related('shares__roommate'):
            actual_items_sum += item.total_price
            total_units = sum(s.units for s in item.shares.all())
            splits_dict = {r.name: Decimal('0.00') for r in roommates}
            splits_str = []

            if total_units > 0:
                for share in item.shares.all():
                    exact_cost = item.total_price * (share.units / total_units)
                    r_items_exact[share.roommate] += exact_cost
                    display_cost = exact_cost.quantize(Decimal('0.01'))
                    splits_dict[share.roommate.name] = display_cost
                    splits_str.append(f"{share.roommate.name}: {share.units:g} (${display_cost})")

            item_rows.append({
                'item': item,
                'splits_str': ', '.join(splits_str),
                'splits_by_roommate': [splits_dict[r.name] for r in roommates],
            })

        # Exact tax per person (e.g. 1.54 / 4 = 0.385)
        exact_tax_per_person = (
            (receipt.tax_amount / Decimal(num_roommates))
            if num_roommates > 0 else Decimal('0.00')
        )
        display_tax_per_person = exact_tax_per_person.quantize(Decimal('0.01'))

        # True receipt total (actual item prices + actual tax)
        true_receipt_total = (actual_items_sum + receipt.tax_amount).quantize(Decimal('0.01'))

        roommate_subtotals = []
        for r in roommates:
            items_rounded = r_items_exact[r].quantize(Decimal('0.01'))
            person_total = (r_items_exact[r] + exact_tax_per_person).quantize(Decimal('0.01'))
            overall_items[r] += r_items_exact[r]
            overall_tax[r] += exact_tax_per_person
            roommate_subtotals.append({
                'roommate': r,
                'items': items_rounded,
                'tax': display_tax_per_person,
                'total': person_total,
            })

        # Adjust 1-2 cent rounding remainder on the payer (Het) so the 4 columns sum to true_receipt_total
        diff = true_receipt_total - sum(sub['total'] for sub in roommate_subtotals)
        if diff != Decimal('0.00') and roommate_subtotals:
            me_sub = next((s for s in roommate_subtotals if s['roommate'].is_me), roommate_subtotals[0])
            me_sub['total'] += diff

        receipt_reports.append({
            'receipt': receipt,
            'item_rows': item_rows,
            'tax_per_person': display_tax_per_person,
            'roommate_subtotals': roommate_subtotals,
            'receipt_grand_total': true_receipt_total,
        })

    # Build final combined Splitwise cards
    summary_rows = []
    for r in roommates:
        items_sub = overall_items[r].quantize(Decimal('0.01'))
        tax_sub = overall_tax[r].quantize(Decimal('0.01'))
        final_tot = (overall_items[r] + overall_tax[r]).quantize(Decimal('0.01'))
        summary_rows.append({
            'roommate': r,
            'items_subtotal': items_sub,
            'tax_share': tax_sub,
            'final_total': final_tot,
        })

    true_grand_total = sum(rep['receipt_grand_total'] for rep in receipt_reports)
    overall_diff = true_grand_total - sum(row['final_total'] for row in summary_rows)
    if overall_diff != Decimal('0.00') and summary_rows:
        me_row = next((row for row in summary_rows if row['roommate'].is_me), summary_rows[0])
        me_row['final_total'] += overall_diff

    return {
        'receipts': receipts,
        'roommates': roommates,
        'receipt_reports': receipt_reports,
        'summary_rows': summary_rows,
        'grand_total': true_grand_total,
    }

def batch_summary(request):
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    if request.method == 'POST' and 'receipt_id' in request.POST:
        r_obj = get_object_or_404(Receipt, id=int(request.POST['receipt_id']))
        r_obj.tax_amount = Decimal(request.POST.get('tax_amount') or '0.00')
        r_obj.save()
        return redirect(f"{reverse('batch_summary')}?ids={ids_param}")

    data = build_summary_data(receipt_ids)
    data['ids_param'] = ids_param
    return render(request, 'splitter/summary.html', data)

def export_latex_pdf(request):
    ids_param = request.GET.get('ids', '')
    receipt_ids = [int(x) for x in ids_param.split(',') if x.isdigit()]
    if not receipt_ids:
        return redirect('receipt_list')

    data = build_summary_data(receipt_ids)
    roommates = data['roommates']

    # Build LaTeX source dynamically
    lines = [
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[margin=0.75in]{geometry}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{xcolor}",
        r"\definecolor{tablehead}{HTML}{F0F4F8}",
        r"\begin{document}",
        r"\section*{Superstore Splitwise Summary}",
        f"\\textbf{{Bills Included:}} {len(data['receipts'])} \\quad | \\quad "
        f"\\textbf{{Combined Grand Total:}} \\${data['grand_total']}\\\\[1em]",
        r"\subsection*{Final Splitwise Totals (Tax Divided Equally)}",
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"\textbf{Roommate} & \textbf{Items Subtotal} & \textbf{Equal Tax Share} & \textbf{Add to Splitwise} \\",
        r"\midrule",
    ]

    for row in data['summary_rows']:
        r_name = latex_escape(row['roommate'].name + (" (Me)" if row['roommate'].is_me else ""))
        lines.append(
            f"{r_name} & \\${row['items_subtotal']} & \\${row['tax_share']} & \\textbf{{\\${row['final_total']}}} \\\\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{1.5em}",
    ]

    # Per-receipt itemized tables
    col_spec = "p{5.2cm}rr" + ("r" * len(roommates))
    r_headers = " & ".join([f"\\textbf{{{latex_escape(r.name)}}}" for r in roommates])

    for rep in data['receipt_reports']:
        rec = rep['receipt']
        mod_str = rec.file_modified_at.strftime('%Y-%m-%d %H:%M') if rec.file_modified_at else 'N/A'
        date_info = f"Order: {latex_escape(rec.order_date_str)} | " if rec.order_date_str else ""
        lines += [
            f"\\subsection*{{Bill: {latex_escape(rec.filename)}}}",
            f"\\small{{{date_info}File Last Modified: {mod_str} \\quad | \\quad Total Bill Tax: \\${rec.tax_amount} (\\${rep['tax_per_person']} each)}}\\\\[0.5em]",
            f"\\begin{{longtable}}{{{col_spec}}}",
            r"\toprule",
            f"\\textbf{{Item}} & \\textbf{{Qty}} & \\textbf{{Price}} & {r_headers} \\\\",
            r"\midrule",
        ]
        for ir in rep['item_rows']:
            it = ir['item']
            r_cols = " & ".join([f"\\${val}" for val in ir['splits_by_roommate']])
            lines.append(
                f"{latex_escape(it.name[:38])} & {latex_escape(it.quantity_str)} & \\${it.total_price} & {r_cols} \\\\"
            )
        lines.append(r"\midrule")
        tax_cols = " & ".join([f"\\${rep['tax_per_person']}" for _ in roommates])
        lines.append(f"\\textit{{Equal Tax Share (1/4)}} & - & \\${rec.tax_amount} & {tax_cols} \\\\")
        lines.append(r"\midrule")
        tot_cols = " & ".join([f"\\textbf{{\\${sub['total']}}}" for sub in rep['roommate_subtotals']])
        lines.append(f"\\textbf{{Bill Total}} & & \\textbf{{\\${rep['receipt_grand_total']}}} & {tot_cols} \\\\")
        lines += [
            r"\bottomrule",
            r"\end{longtable}",
            r"\vspace{0.5em}",
        ]

    lines.append(r"\end{document}")
    tex_source = "\n".join(lines)

    # Compile to PDF via pdflatex
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "splitwise_summary.tex")
        pdf_path = os.path.join(tmpdir, "splitwise_summary.pdf")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_source)

        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            with open(pdf_path, "rb") as pdf_file:
                response = HttpResponse(pdf_file.read(), content_type="application/pdf")
                response["Content-Disposition"] = 'attachment; filename="Splitwise_Monthly_Summary.pdf"'
                return response
        except (FileNotFoundError, subprocess.CalledProcessError):
            # Fallback: download the .tex file if pdflatex encountered an issue
            response = HttpResponse(tex_source, content_type="text/plain")
            response["Content-Disposition"] = 'attachment; filename="Splitwise_Monthly_Summary.tex"'
            return response