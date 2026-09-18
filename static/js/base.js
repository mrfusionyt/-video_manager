/* ============================================================
   base.js — общий JS из base.html
   • мобильный drawer (hamburger)
   • Show/Hide категорий в сайдбаре
   • обработка data-sort (dropdown "Sort")
   • switchProfile — переключатель Female / Transgender
   ============================================================ */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var hamburgerBtn        = document.getElementById('hamburgerBtn');
        var mobileDrawer        = document.getElementById('mobileDrawer');
        var mobileDrawerOverlay = document.getElementById('mobileDrawerOverlay');
        var mobileDrawerClose   = document.getElementById('mobileDrawerClose');

        function openDrawer() {
            if (!mobileDrawer) return;
            mobileDrawer.classList.add('open');
            if (mobileDrawerOverlay) mobileDrawerOverlay.classList.add('open');
            document.body.style.overflow = 'hidden';
        }
        function closeDrawer() {
            if (!mobileDrawer) return;
            mobileDrawer.classList.remove('open');
            if (mobileDrawerOverlay) mobileDrawerOverlay.classList.remove('open');
            document.body.style.overflow = '';
        }

        if (hamburgerBtn) hamburgerBtn.addEventListener('click', function (e) { e.preventDefault(); openDrawer(); });
        if (mobileDrawerClose) mobileDrawerClose.addEventListener('click', closeDrawer);
        if (mobileDrawerOverlay) mobileDrawerOverlay.addEventListener('click', closeDrawer);

        if (mobileDrawer) {
            mobileDrawer.querySelectorAll('a').forEach(function (link) {
                link.addEventListener('click', function () { closeDrawer(); });
            });
        }

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeDrawer();
        });

        var toggleBtn = document.getElementById('toggleCategoriesBtn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function (e) {
                e.preventDefault();
                var extras = document.querySelectorAll('.category-extra');
                var show = false;
                if (extras.length > 0 && extras[0].style.display === 'none') show = true;
                extras.forEach(function (el) { el.style.display = show ? 'list-item' : 'none'; });
                this.textContent = show ? 'Hide' : 'Show All';
            });
        }

        var sortItems = document.querySelectorAll('[data-sort]');
        sortItems.forEach(function (item) {
            item.addEventListener('click', function (e) {
                e.preventDefault();
                var sort = this.dataset.sort;
                var url  = new URL(window.location.href);
                url.searchParams.set('sort', sort);
                url.searchParams.delete('page');
                window.location.href = url.toString();
            });
        });
    });

    window.switchProfile = function (profile) {
        var url = new URL(window.location.href);
        url.searchParams.set('profile', profile);
        url.searchParams.delete('page');
        window.location.href = url.toString();
    };
})();