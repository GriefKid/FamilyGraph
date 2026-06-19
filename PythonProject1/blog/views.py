from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Post
from .forms import PostForm

def blog_home(request):
    posts = Post.objects.filter(published=True)
    return render(request, 'blog/blog_home.html', {'posts': posts})

@login_required
def user_dashboard(request):
    # همه پست‌های همین کاربر
    posts = Post.objects.filter(author=request.user)
    return render(request, 'blog/dashboard.html', {'posts': posts})

@login_required
def post_create(request):
    if request.method == 'POST':
        form = PostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user  # اینجا پست به کاربر لاگین‌شده وصل می‌شود
            post.save()
            return redirect('user_dashboard')
    else:
        form = PostForm()
    return render(request, 'blog/post_form.html', {'form': form})

@login_required
def post_edit(request, pk):
    post = get_object_or_404(Post, pk=pk, author=request.user)
    if request.method == 'POST':
        form = PostForm(request.POST, instance=post)
        if form.is_valid():
            form.save()
            return redirect('user_dashboard')
    else:
        form = PostForm(instance=post)
    return render(request, 'blog/post_form.html', {'form': form})