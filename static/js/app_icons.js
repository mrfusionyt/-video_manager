/* Adds a consistent, lightweight inline SVG icon set without changing routes or handlers. */
(() => {
  const icons = {
    videos: '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3z"/>',
    photo: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    artists: '<circle cx="9" cy="8" r="3.5"/><path d="M3 21a6 6 0 0 1 12 0M16 4.5a3.5 3.5 0 0 1 0 7M18 15a5 5 0 0 1 3 6"/>',
    folders: '<path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 9h18"/>',
    settings: '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.6a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.6-1.4-2.4 1.4-1.1a7 7 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.6a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.6 1.4 2.4-1.4 1.1a7 7 0 0 1 0 2Z"/>',
    tools: '<path d="m14.7 6.3 3 3M4 20l5.5-5.5M14 4a5 5 0 0 0 6 6l-4 4-6-6 4-4Z"/><path d="m3 21 5-5"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 5 5"/>',
    filter: '<path d="M4 5h16l-6.2 7.2v5.3l-3.6 1.8v-7.1z"/>',
    sort: '<path d="M8 5v14M4.5 8.5 8 5l3.5 3.5M16 19V5m-3.5 10.5L16 19l3.5-3.5"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 20h16"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9A7 7 0 0 1 18 6l2 1M4 17l2-1a7 7 0 0 0 12.4-3"/>',
    duplicate: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    play: '<path d="m8 5 11 7-11 7z"/>',
    back: '<path d="m15 18-6-6 6-6M9 12h12"/>',
    library: '<path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"/>',
    user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 21a7 7 0 0 1 14 0"/>',
    grid: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  };
  const rules = [
    [/^videos?$/, 'videos'], [/^photo(s)?$/, 'photo'], [/^artists?$/, 'artists'], [/^folders?$/, 'folders'],
    [/^settings$/, 'settings'], [/^library maintenance$/, 'tools'], [/^youtube downloader$/, 'download'],
    [/^instagram downloader$/, 'photo'], [/^photoclassifier$/, 'photo'], [/^download vr$/, 'download'],
    [/^duplicates?$/, 'duplicate'], [/^all videos$/, 'videos'], [/^all photos$/, 'photo'], [/^all artists$/, 'artists'],
    [/^filters?$/, 'filter'], [/^sort$/, 'sort'], [/^search$/, 'search'], [/^go$/, 'search'],
    [/^play( all)?$/, 'play'], [/^back( to settings)?$/, 'back'], [/^library( maintenance)?$/, 'library'],
  ];
  const norm = el => (el.dataset.vmIconLabel || el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
  function addIcon(el, name) {
    if (el.querySelector(':scope > .vm-icon')) return;
    const span = document.createElement('span');
    span.className = 'vm-icon'; span.setAttribute('aria-hidden', 'true');
    span.innerHTML = `<svg viewBox="0 0 24 24">${icons[name]}</svg>`;
    el.classList.add('vm-icon-target'); el.insertBefore(span, el.firstChild);
  }
  function apply() {
    document.querySelectorAll('.sidebar .nav-link, .top-bar .nav-tabs-custom .nav-link, .top-bar button, .top-bar .btn-red, .top-bar .btn-search').forEach(el => {
      const label = norm(el).replace(/\s*\d+\s*$/, '').trim();
      const rule = rules.find(([re]) => re.test(label));
      if (rule) addIcon(el, rule[1]);
    });
    document.querySelectorAll('.sidebar h5').forEach(el => {
      const label = norm(el);
      if (label === 'settings') addIcon(el, 'settings');
      else if (label === 'tools') addIcon(el, 'tools');
      else if (label === 'artists') addIcon(el, 'artists');
      else if (label.includes('categor')) addIcon(el, 'grid');
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, { once: true }); else apply();
})();
