/* ============================================================
   index.js — JS главной страницы (/)
   • More (бесконечная загрузка)
   • пагинация
   • сортировка через data-sort (обрабатывается в base.js тоже)
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

    function renderPagination(page, total) {
        if (!paginationContainer) return;
        if (total <= 1 || ['top10', 'top50', 'top100'].indexOf(currentSort) !== -1) {
            paginationContainer.innerHTML = '';
            return;
        }
        var html = '<nav aria-label="Page navigation"><ul class="pagination justify-content-center">';
        if (page > 1) {
            html += '<li class="page-item"><a class="page-link" href="#" data-page="' + (page - 1) + '">Previous</a></li>';
        }
        for (var p = 1; p <= total; p++) {
            if (p === page) {
                html += '<li class="page-item active"><span class="page-link">' + p + '</span></li>';
            } else {
                html += '<li class="page-item"><a class="page-link" href="#" data-page="' + p + '">' + p + '</a></li>';
            }
        }
        if (page < total) {
            html += '<li class="page-item"><a class="page-link" href="#" data-page="' + (page + 1) + '">Next</a></li>';
        }
        html += '</ul></nav>';
        paginationContainer.innerHTML = html;

        paginationContainer.querySelectorAll('a.page-link').forEach(function (link) {
            link.addEventListener('click', function (e) {
                e.preventDefault();
                var pageNum = parseInt(this.dataset.page, 10);
                if (!pageNum) return;
                var url = '/?page=' + pageNum + '&sort=' + currentSort + '&search=' + encodeURIComponent(currentSearch);
                if (currentFolder) url += '&folder=' + encodeURIComponent(currentFolder);
                if (selectedCategories.length) url += '&category=' + selectedCategories.join('&category=');
                url += '&profile=' + currentProfile;
                window.location.href = url;
            });
        });
    }

    if (moreBtn) {
        moreBtn.addEventListener('click', function () {
            if (currentPage >= totalPages || ['top10', 'top50', 'top100'].indexOf(currentSort) !== -1) {
                if (moreContainer) moreContainer.style.display = 'none';
                return;
            }
            var nextPage = currentPage + 1;
            var url = '/load_more?page=' + nextPage + '&sort=' + currentSort + '&search=' + encodeURIComponent(currentSearch);
            if (currentFolder) url += '&folder=' + encodeURIComponent(currentFolder);
            if (selectedCategories.length) url += '&category=' + selectedCategories.join('&category=');
            url += '&profile=' + currentProfile;

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