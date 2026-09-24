/* ============================================================
   index.js — JS главной страницы (/)
   • More (бесконечная загрузка)
   • пагинация (компактная, с ellipsis)
   • сортировка через data-sort (обрабатывается в base.js)
   • hover-превью новых карточек через PreviewHover.scan()
   Конфиг приходит через window.INDEX_CONFIG из шаблона.
   ============================================================ */
(function () {
    'use strict';

    var CFG = window.INDEX_CONFIG || {};

    var grid                = document.getElementById('videoGrid');
    var moreContainer       = document.getElementById('moreContainer');
    var moreBtn             = document.getElementById('moreBtn');
    var paginationContainer = document.getElementById('paginationContainer');

    if (!moreBtn || !grid) return;

    var currentPage        = parseInt(CFG.page || 1, 10) || 1;
    var totalPages         = parseInt(CFG.totalPages || 1, 10) || 1;
    var selectedCategories = CFG.selectedCategories || [];
    var currentSort        = CFG.sort || 'date';
    var currentSearch      = CFG.search || '';
    var currentFolder      = CFG.folder || '';
    var currentProfile     = CFG.profile || 'female';

    /* -----------------------------------------------------------
       buildPageRange(current, total) — зеркало paginate_range()
       Возвращает [1, 2, 3, 4, 5, '...', 81] и т.п.
       ----------------------------------------------------------- */
    function buildPageRange(current, total) {
        var side = 2;
        var edge = 5;

        if (total <= 0) return [];
        if (total <= 7) {
            var arr = [];
            for (var i = 1; i <= total; i++) arr.push(i);
            return arr;
        }

        var set = {};
        set[1] = true;
        set[total] = true;

        for (var i = current - side; i <= current + side; i++) {
            if (i >= 1 && i <= total) set[i] = true;
        }
        if (current <= edge) {
            var hi = Math.min(edge, total);
            for (var i = 1; i <= hi; i++) set[i] = true;
        }
        if (current >= total - edge + 1) {
            var lo = Math.max(1, total - edge + 1);
            for (var i = lo; i <= total; i++) set[i] = true;
        }

        var nums = Object.keys(set).map(Number).sort(function (a, b) { return a - b; });
        var result = [];
        var prev = 0;
        for (var i = 0; i < nums.length; i++) {
            var p = nums[i];
            if (prev && p - prev > 1) result.push('...');
            result.push(p);
            prev = p;
        }
        return result;
    }

    /* -----------------------------------------------------------
       buildPageUrl(page) — собирает URL текущего фильтра
       ----------------------------------------------------------- */
    function buildPageUrl(page) {
        var url = '/?page=' + page
                + '&sort=' + encodeURIComponent(currentSort)
                + '&search=' + encodeURIComponent(currentSearch || '');
        if (currentFolder) {
            url += '&folder=' + encodeURIComponent(currentFolder);
        }
        if (selectedCategories.length) {
            url += '&category=' + selectedCategories.join('&category=');
        }
        url += '&profile=' + encodeURIComponent(currentProfile);
        return url;
    }

    /* -----------------------------------------------------------
       renderPagination(page, total)
       ----------------------------------------------------------- */
    function renderPagination(page, total) {
        if (!paginationContainer) return;

        if (total <= 1 || ['top10', 'top50', 'top100'].indexOf(currentSort) !== -1) {
            paginationContainer.innerHTML = '';
            return;
        }

        var pages = buildPageRange(page, total);
        var html = '<nav aria-label="Page navigation"><ul class="pagination justify-content-center">';

        // Prev
        if (page > 1) {
            html += '<li class="page-item"><a class="page-link" href="#" data-page="' + (page - 1) + '">‹ Prev</a></li>';
        } else {
            html += '<li class="page-item disabled"><span class="page-link">‹ Prev</span></li>';
        }

        // Numbers with ellipsis
        for (var i = 0; i < pages.length; i++) {
            var p = pages[i];
            if (p === '...') {
                html += '<li class="page-item disabled"><span class="page-link">…</span></li>';
            } else if (p === page) {
                html += '<li class="page-item active"><span class="page-link">' + p + '</span></li>';
            } else {
                html += '<li class="page-item"><a class="page-link" href="#" data-page="' + p + '">' + p + '</a></li>';
            }
        }

        // Next
        if (page < total) {
            html += '<li class="page-item"><a class="page-link" href="#" data-page="' + (page + 1) + '">Next ›</a></li>';
        } else {
            html += '<li class="page-item disabled"><span class="page-link">Next ›</span></li>';
        }

        html += '</ul></nav>';
        paginationContainer.innerHTML = html;

        paginationContainer.querySelectorAll('a.page-link').forEach(function (link) {
            link.addEventListener('click', function (e) {
                e.preventDefault();
                var pageNum = parseInt(this.dataset.page, 10);
                if (!pageNum) return;
                window.location.href = buildPageUrl(pageNum);
            });
        });
    }

    /* -----------------------------------------------------------
       More — AJAX-подгрузка
       ----------------------------------------------------------- */
    if (moreBtn) {
        moreBtn.addEventListener('click', function () {
            if (currentPage >= totalPages || ['top10', 'top50', 'top100'].indexOf(currentSort) !== -1) {
                if (moreContainer) moreContainer.style.display = 'none';
                return;
            }
            var nextPage = currentPage + 1;

            var url = '/load_more?page=' + nextPage
                    + '&sort=' + encodeURIComponent(currentSort)
                    + '&search=' + encodeURIComponent(currentSearch || '');
            if (currentFolder) url += '&folder=' + encodeURIComponent(currentFolder);
            if (selectedCategories.length) url += '&category=' + selectedCategories.join('&category=');
            url += '&profile=' + encodeURIComponent(currentProfile);

            fetch(url)
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.html) {
                        grid.insertAdjacentHTML('beforeend', data.html);
                    }
                    currentPage = data.page;
                    totalPages  = data.total_pages;
                    renderPagination(currentPage, totalPages);
                    if (currentPage >= totalPages || !data.has_more) {
                        if (moreContainer) moreContainer.style.display = 'none';
                    }
                    // Активируем hover-превью на новых карточках
                    if (window.PreviewHover) window.PreviewHover.scan(grid);
                })
                .catch(function (err) { console.error('Error loading more:', err); });
        });
    }

    renderPagination(currentPage, totalPages);
    if (currentPage >= totalPages || ['top10', 'top50', 'top100'].indexOf(currentSort) !== -1) {
        if (moreContainer) moreContainer.style.display = 'none';
    }
})();