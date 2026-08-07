from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def people_hub(request):
    return render(request, 'hubs/people.html')


@login_required
def insight_hub(request):
    return render(request, 'hubs/insight.html')


@login_required
def relationship_work_hub(request):
    return render(request, 'hubs/relationship_work.html')


@login_required
def import_hub(request):
    return render(request, 'hubs/import.html')
