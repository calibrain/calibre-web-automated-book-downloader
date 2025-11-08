// Modern UI script: search, cards, details, downloads, status, theme
// Reuses existing API endpoints. Keeps logic minimal and accessible.

(function () {
  // ---- DOM ----
  const el = {
    searchInput: document.getElementById('search-input'),
    searchBtn: document.getElementById('search-button'),
    searchIcon: document.getElementById('search-icon'),
    searchSpinner: document.getElementById('search-spinner'),
    searchSection: document.getElementById('search-section'),
    advToggle: document.getElementById('toggle-advanced'),
    filtersForm: document.getElementById('search-filters'),
    isbn: document.getElementById('isbn-input'),
    author: document.getElementById('author-input'),
    title: document.getElementById('title-input'),
    lang: document.getElementById('lang-input'),
    sort: document.getElementById('sort-input'),
    content: document.getElementById('content-input'),
    resultsGrid: document.getElementById('results-grid'),
    resultsSection: document.getElementById('results-section'),
    noResults: document.getElementById('no-results'),
    modalOverlay: document.getElementById('modal-overlay'),
    detailsContainer: document.getElementById('details-container'),
    refreshStatusBtn: document.getElementById('refresh-status-button'),
    clearCompletedBtn: document.getElementById('clear-completed-button'),
    statusLoading: document.getElementById('status-loading'),
    statusList: document.getElementById('status-list'),
    statusSection: document.getElementById('status-section'),
    activeDownloadsCount: document.getElementById('active-downloads-count'),
    // Active downloads (top section under search)
    activeTopSec: document.getElementById('active-downloads-top'),
    activeTopList: document.getElementById('active-downloads-list'),
    activeTopRefreshBtn: document.getElementById('active-refresh-button'),
    themeToggle: document.getElementById('theme-toggle'),
    themeText: document.getElementById('theme-text'),
    themeMenu: document.getElementById('theme-menu')
  };

  // ---- Constants ----
  const API = {
    search: '/request/api/search',
    info: '/request/api/info',
    download: '/request/api/download',
    status: '/request/api/status',
    cancelDownload: '/request/api/download',
    setPriority: '/request/api/queue',
    clearCompleted: '/request/api/queue/clear',
    activeDownloads: '/request/api/downloads/active'
  };
  const FILTERS = ['isbn', 'author', 'title', 'lang', 'sort', 'content', 'format'];
  
  // Track current status for button state management
  let currentStatus = {};
  let previousStatus = {};

  // ---- Utils ----
  const utils = {
    show(node) { node && node.classList.remove('hidden'); },
    hide(node) { node && node.classList.add('hidden'); },
    async j(url, opts = {}) {
      const res = await fetch(url, opts);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.json();
    },
    // Build query string from basic + advanced filters
    buildQuery() {
      const q = [];
      const basic = el.searchInput?.value?.trim();
      if (basic) q.push(`query=${encodeURIComponent(basic)}`);

      if (!el.filtersForm || el.filtersForm.classList.contains('hidden')) {
        return q.join('&');
      }

      FILTERS.forEach((name) => {
        if (name === 'format') {
          const checked = Array.from(document.querySelectorAll('[id^="format-"]:checked'));
          checked.forEach((cb) => q.push(`format=${encodeURIComponent(cb.value)}`));
        } else {
          const input = document.querySelectorAll(`[id^="${name}-input"]`);
          input.forEach((node) => {
            const val = node.value?.trim();
            if (val) q.push(`${name}=${encodeURIComponent(val)}`);
          });
        }
      });

      return q.join('&');
    },
    // Simple notification via alert fallback
    toast(msg) { try { console.info(msg); } catch (_) {} },
    // Escapes text for safe HTML injection
    e(text) { return (text ?? '').toString(); },
    // Get button state for a book ID
    getButtonState(bookId) {
      if (!currentStatus) return null;
      if (currentStatus.downloading && currentStatus.downloading[bookId]) {
        return { text: 'Downloading', state: 'downloading' };
      }
      if (currentStatus.queued && currentStatus.queued[bookId]) {
        return { text: 'Queued', state: 'queued' };
      }
      return { text: 'Download', state: 'download' };
    },
    // Set download button to queuing state (immediate feedback)
    setDownloadButtonQueuing(button) {
      if (!button) return;
      
      // Disable button
      button.disabled = true;
      
      // Update button text
      const textSpan = button.querySelector('.download-button-text');
      if (textSpan) {
        textSpan.textContent = 'Queuing...';
        // Force layout recalculation on mobile to prevent text clipping
        if (window.innerWidth <= 639) {
          void textSpan.offsetWidth; // Force reflow
        }
      } else {
        button.textContent = 'Queuing...';
      }
      
      // Show spinner
      const spinner = button.querySelector('.download-spinner');
      if (spinner) {
        spinner.classList.remove('hidden');
      }
      
      // Update button color to indicate processing (use blue color)
      const classes = button.className.split(' ');
      const filteredClasses = classes.filter(cls => 
        !cls.match(/^bg-(blue|green|yellow|orange)-\d+$/) && !cls.match(/^hover:bg-(blue|green|yellow|orange)-\d+$/)
      );
      button.className = filteredClasses.join(' ');
      button.classList.add('bg-blue-600', 'hover:bg-blue-700');
    }
  };

  // ---- Modal ----
  const modal = {
    open() { el.modalOverlay?.classList.add('active'); },
    close() { el.modalOverlay?.classList.remove('active'); el.detailsContainer.innerHTML = ''; }
  };

  // ---- Cards ----
  function renderCard(book) {
    const cover = book.preview ? `<img src="${utils.e(book.preview)}" alt="Cover" class="book-card-cover w-full h-88 object-cover rounded">` :
      `<div class="book-card-cover w-full h-88 rounded flex items-center justify-center opacity-70" style="background: var(--bg-soft)">No Cover</div>`;

    // Get button state
    const buttonState = utils.getButtonState(book.id);
    const buttonText = buttonState ? buttonState.text : 'Download';
    const buttonStateClass = buttonState && buttonState.state !== 'download' 
      ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed' 
      : 'bg-blue-600 hover:bg-blue-700';
    const isDisabled = buttonState && buttonState.state !== 'download';

    const html = `
      <article class="book-card rounded border p-3 flex flex-col gap-3" style="border-color: var(--border-muted); background: var(--bg-soft)">
        <div class="book-card-content flex flex-col gap-3">
          ${cover}
          <div class="book-card-text flex-1 space-y-1">
            <h3 class="font-semibold leading-tight">${utils.e(book.title) || 'Untitled'}</h3>
            <p class="text-sm opacity-80">${utils.e(book.author) || 'Unknown author'}</p>
            <div class="text-xs opacity-70 flex flex-wrap gap-2">
              <span>${utils.e(book.year) || '-'}</span>
              <span>•</span>
              <span>${utils.e(book.language) || '-'}</span>
              <span>•</span>
              <span>${utils.e(book.format) || '-'}</span>
              ${book.size ? `<span>•</span><span>${utils.e(book.size)}</span>` : ''}
            </div>
          </div>
        </div>
        <div class="book-card-buttons flex gap-2">
          <button class="px-3 py-2 rounded border text-sm flex-1 flex items-center justify-center gap-2" data-action="details" data-id="${utils.e(book.id)}" style="border-color: var(--border-muted);">
            <span class="details-button-text">Details</span>
            <div class="details-spinner hidden w-4 h-4 border-2 border-current border-t-transparent rounded-full"></div>
          </button>
          <button class="px-3 py-2 rounded ${buttonStateClass} text-white text-sm flex-1 flex items-center justify-center gap-2" data-action="download" data-id="${utils.e(book.id)}" ${isDisabled ? 'disabled' : ''}>
            <span class="download-button-text">${buttonText}</span>
            <div class="download-spinner hidden w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
          </button>
        </div>
      </article>`;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    // Bind actions
    const detailsBtn = wrapper.querySelector('[data-action="details"]');
    const downloadBtn = wrapper.querySelector('[data-action="download"]');
    detailsBtn?.addEventListener('click', () => bookDetails.show(book.id));
    if (!isDisabled) {
      downloadBtn?.addEventListener('click', () => {
        // Show immediate feedback
        utils.setDownloadButtonQueuing(downloadBtn);
        // Then make the API call
        bookDetails.download(book);
      });
    }
    return wrapper.firstElementChild;
  }

  function renderCards(books) {
    el.resultsGrid.innerHTML = '';
    if (!books || books.length === 0) {
      utils.hide(el.resultsSection);
      utils.hide(el.noResults);
      updateSearchSectionPosition();
      return;
    }
    utils.show(el.resultsSection);
    utils.hide(el.noResults);
    const frag = document.createDocumentFragment();
    books.forEach((b) => frag.appendChild(renderCard(b)));
    el.resultsGrid.appendChild(frag);
    updateSearchSectionPosition();
  }

  // Update search section position based on whether we're in initial state
  function updateSearchSectionPosition() {
    if (!el.searchSection) return;
    const hasResults = el.resultsSection && !el.resultsSection.classList.contains('hidden');
    const hasStatus = el.statusSection && !el.statusSection.classList.contains('hidden');
    const hasActiveDownloads = el.activeTopSec && !el.activeTopSec.classList.contains('hidden');
    
    // In initial state when nothing is visible
    if (!hasResults && !hasStatus && !hasActiveDownloads) {
      el.searchSection.classList.add('search-initial-state');
    } else {
      el.searchSection.classList.remove('search-initial-state');
    }
  }
  
  // Update button states for all cards based on current status
  function updateCardButtons() {
    const downloadButtons = el.resultsGrid.querySelectorAll('[data-action="download"]');
    downloadButtons.forEach((btn) => {
      const bookId = btn.getAttribute('data-id');
      const buttonState = utils.getButtonState(bookId);
      
      // Check if button is in "Queuing..." state
      const textSpan = btn.querySelector('.download-button-text');
      const currentText = textSpan ? textSpan.textContent : btn.textContent;
      const isQueuing = currentText === 'Queuing...';
      
      // If button is queuing, only update if we have queued/downloading status
      // Otherwise preserve the queuing state
      if (isQueuing) {
        if (!buttonState || buttonState.state === 'download') {
          return; // Don't update, keep queuing state
        }
      }
      
      if (buttonState) {
        const isDisabled = buttonState.state !== 'download';
        btn.disabled = isDisabled;
        
        // Update button text
        if (textSpan) {
          textSpan.textContent = buttonState.text;
          // Force layout recalculation on mobile to prevent text clipping
          if (window.innerWidth <= 639) {
            void textSpan.offsetWidth; // Force reflow
          }
        } else {
          btn.textContent = buttonState.text;
        }
        
        // Show/hide spinner based on state
        const spinner = btn.querySelector('.download-spinner');
        if (spinner) {
          if (buttonState.state !== 'download') {
            spinner.classList.remove('hidden');
          } else {
            spinner.classList.add('hidden');
          }
        }
        
        // Remove existing color classes (preserve other classes)
        const classes = btn.className.split(' ');
        const filteredClasses = classes.filter(cls => 
          !cls.match(/^bg-(blue|green)-\d+$/) && !cls.match(/^hover:bg-(blue|green)-\d+$/)
        );
        btn.className = filteredClasses.join(' ');
        
        if (buttonState.state !== 'download') {
          // Queued or Downloading state - green
          btn.classList.add('bg-green-600', 'hover:bg-green-700');
          if (isDisabled) {
            btn.classList.add('disabled:opacity-60', 'disabled:cursor-not-allowed');
          }
        } else {
          // Download state - blue
          btn.classList.add('bg-blue-600', 'hover:bg-blue-700');
          btn.classList.remove('disabled:opacity-60', 'disabled:cursor-not-allowed');
        }
      }
    });
  }

  // Update button state for details pane download button
  function updateDetailsPaneButton() {
    const downloadBtn = document.getElementById('download-button');
    if (!downloadBtn) return;
    
    // Get book ID from the details container - we need to store it when modal opens
    const bookId = downloadBtn.getAttribute('data-id');
    if (!bookId) return;
    
    const buttonState = utils.getButtonState(bookId);
    
    // Check if button is in "Queuing..." state
    const textSpan = downloadBtn.querySelector('.download-button-text');
    const currentText = textSpan ? textSpan.textContent : downloadBtn.textContent;
    const isQueuing = currentText === 'Queuing...';
    
    // If button is queuing, only update if we have queued/downloading status
    // Otherwise preserve the queuing state
    if (isQueuing) {
      if (!buttonState || buttonState.state === 'download') {
        return; // Don't update, keep queuing state
      }
    }
    
    if (buttonState) {
      const isDisabled = buttonState.state !== 'download';
      downloadBtn.disabled = isDisabled;
      
      // Update button text
      if (textSpan) {
        textSpan.textContent = buttonState.text;
        // Force layout recalculation on mobile to prevent text clipping
        if (window.innerWidth <= 639) {
          void textSpan.offsetWidth; // Force reflow
        }
      } else {
        downloadBtn.textContent = buttonState.text;
      }
      
      // Show/hide spinner based on state
      const spinner = downloadBtn.querySelector('.download-spinner');
      if (spinner) {
        if (buttonState.state !== 'download') {
          spinner.classList.remove('hidden');
        } else {
          spinner.classList.add('hidden');
        }
      }
      
      // Remove existing color classes (preserve other classes)
      const classes = downloadBtn.className.split(' ');
      const filteredClasses = classes.filter(cls => 
        !cls.match(/^bg-(blue|green)-\d+$/) && !cls.match(/^hover:bg-(blue|green)-\d+$/) &&
        !cls.match(/^disabled:opacity-\d+$/) && !cls.match(/^disabled:cursor-not-allowed$/)
      );
      downloadBtn.className = filteredClasses.join(' ');
      
      if (buttonState.state !== 'download') {
        // Queued or Downloading state - green
        downloadBtn.classList.add('bg-green-600', 'hover:bg-green-700');
        if (isDisabled) {
          downloadBtn.classList.add('disabled:opacity-60', 'disabled:cursor-not-allowed');
        }
      } else {
        // Download state - blue
        downloadBtn.classList.add('bg-blue-600', 'hover:bg-blue-700');
        downloadBtn.classList.remove('disabled:opacity-60', 'disabled:cursor-not-allowed');
      }
    }
  }

  // ---- Search ----
  const search = {
    setLoading(isLoading) {
      if (!el.searchBtn || !el.searchIcon || !el.searchSpinner) return;
      if (isLoading) {
        el.searchBtn.disabled = true;
        el.searchIcon.classList.add('hidden');
        el.searchSpinner.classList.remove('hidden');
      } else {
        el.searchBtn.disabled = false;
        el.searchIcon.classList.remove('hidden');
        el.searchSpinner.classList.add('hidden');
      }
    },
    async run() {
      const qs = utils.buildQuery();
      if (!qs) { renderCards([]); return; }
      this.setLoading(true);
      try {
        const data = await utils.j(`${API.search}?${qs}`);
        renderCards(data);
      } catch (e) {
        renderCards([]);
      } finally {
        this.setLoading(false);
      }
    }
  };

  // ---- Details ----
  const bookDetails = {
    async show(id) {
      // Find the Details button for this book ID
      const detailsBtn = el.resultsGrid.querySelector(`[data-action="details"][data-id="${id}"]`);
      let originalButtonState = null;
      
      if (detailsBtn) {
        // Store original button state
        const textSpan = detailsBtn.querySelector('.details-button-text');
        const spinner = detailsBtn.querySelector('.details-spinner');
        originalButtonState = {
          text: textSpan ? textSpan.textContent : detailsBtn.textContent,
          disabled: detailsBtn.disabled,
          spinnerHidden: spinner ? spinner.classList.contains('hidden') : true
        };
        
        // Update button to show loading state
        detailsBtn.disabled = true;
        if (textSpan) {
          textSpan.textContent = 'Loading';
        } else {
          detailsBtn.textContent = 'Loading';
        }
        if (spinner) {
          spinner.classList.remove('hidden');
        }
      }
      
      try {
        // Don't open modal yet - wait for data to load
        const book = await utils.j(`${API.info}?id=${encodeURIComponent(id)}`);
        
        // Now open the modal with the loaded data
        modal.open();
        el.detailsContainer.innerHTML = this.tpl(book);
        document.getElementById('close-details')?.addEventListener('click', modal.close);
        const detailsDownloadBtn = document.getElementById('download-button');
        detailsDownloadBtn?.addEventListener('click', () => this.download(book, detailsDownloadBtn));
      } catch (e) {
        // Open modal even on error to show error message
        modal.open();
        el.detailsContainer.innerHTML = '<div class="p-4">Failed to load details.</div>';
      } finally {
        // Restore button state
        if (detailsBtn && originalButtonState) {
          detailsBtn.disabled = originalButtonState.disabled;
          const textSpan = detailsBtn.querySelector('.details-button-text');
          const spinner = detailsBtn.querySelector('.details-spinner');
          if (textSpan) {
            textSpan.textContent = originalButtonState.text;
          } else {
            detailsBtn.textContent = originalButtonState.text;
          }
          if (spinner) {
            if (originalButtonState.spinnerHidden) {
              spinner.classList.add('hidden');
            } else {
              spinner.classList.remove('hidden');
            }
          }
        }
      }
    },
    tpl(book) {
      const cover = book.preview ? `<img src="${utils.e(book.preview)}" alt="Cover" class="w-full h-88 object-cover rounded">` : '';
      const infoList = book.info ? Object.entries(book.info).map(([k, v]) => `<li><strong>${utils.e(k)}:</strong> ${utils.e((v||[]).join 
        ? v.join(', ') : v)}</li>`).join('') : '';
      
      // Get button state for modal
      const buttonState = utils.getButtonState(book.id);
      const buttonText = buttonState ? buttonState.text : 'Download';
      const buttonStateClass = buttonState && buttonState.state !== 'download' 
        ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed' 
        : 'bg-blue-600 hover:bg-blue-700';
      const isDisabled = buttonState && buttonState.state !== 'download';
      
      return `
        <div class="p-4 space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>${cover}</div>
            <div>
              <h3 class="text-lg font-semibold mb-1">${utils.e(book.title) || 'Untitled'}</h3>
              <p class="text-sm opacity-80">${utils.e(book.author) || 'Unknown author'}</p>
              <div class="text-sm mt-2 space-y-1">
                <p><strong>Publisher:</strong> ${utils.e(book.publisher) || '-'}</p>
                <p><strong>Year:</strong> ${utils.e(book.year) || '-'}</p>
                <p><strong>Language:</strong> ${utils.e(book.language) || '-'}</p>
                <p><strong>Format:</strong> ${utils.e(book.format) || '-'}</p>
                <p><strong>Size:</strong> ${utils.e(book.size) || '-'}</p>
              </div>
            </div>
          </div>
          ${infoList ? `<div><h4 class="font-semibold mb-2">Further Information</h4><ul class="list-disc pl-6 space-y-1 text-sm">${infoList}</ul></div>` : ''}
          <div class="flex gap-2">
            <button id="download-button" data-id="${utils.e(book.id)}" class="px-3 py-2 rounded ${buttonStateClass} text-white text-sm flex items-center justify-center gap-2" ${isDisabled ? 'disabled' : ''}>
              <span class="download-button-text">${buttonText}</span>
              <div class="download-spinner ${buttonState && buttonState.state !== 'download' ? '' : 'hidden'} w-4 h-4 border-2 border-white border-t-transparent rounded-full"></div>
            </button>
            <button id="close-details" class="px-3 py-2 rounded border text-sm" style="border-color: var(--border-muted);">Close</button>
          </div>
        </div>`;
    },
    async download(book, button = null) {
      if (!book) return;
      
      // Show immediate feedback if button is provided
      if (button) {
        utils.setDownloadButtonQueuing(button);
      }
      
      try {
        await utils.j(`${API.download}?id=${encodeURIComponent(book.id)}`);
        utils.toast('Queued for download');
        modal.close();
        status.fetch();
      } catch (_){}
    }
  };

  // ---- Status ----
  const status = {
    async fetch() {
      try {
        utils.show(el.statusLoading);
        const data = await utils.j(API.status);
        // Store previous status before updating
        previousStatus = JSON.parse(JSON.stringify(currentStatus));
        currentStatus = data;
        this.render(data);
        // Also reflect active downloads in the top section
        this.renderTop(data);
        this.updateActive();
        // Update card button states
        updateCardButtons();
        // Update details pane button state
        updateDetailsPaneButton();
        // Detect changes and show toasts
        this.detectChanges(previousStatus, currentStatus);
      } catch (e) {
        el.statusList.innerHTML = '<div class="text-sm opacity-80">Error loading status.</div>';
      } finally { utils.hide(el.statusLoading); }
    },
    detectChanges(prev, curr) {
      if (!prev || Object.keys(prev).length === 0) return;
      
      // Check for new items in queue
      const prevQueued = prev.queued || {};
      const currQueued = curr.queued || {};
      Object.keys(currQueued).forEach((bookId) => {
        if (!prevQueued[bookId]) {
          const book = currQueued[bookId];
          toastNotifications.show(`${book.title || 'Book'} added to queue`, 'info');
        }
      });
      
      // Check for items that started downloading
      const prevDownloading = prev.downloading || {};
      const currDownloading = curr.downloading || {};
      Object.keys(currDownloading).forEach((bookId) => {
        if (!prevDownloading[bookId]) {
          const book = currDownloading[bookId];
          toastNotifications.show(`${book.title || 'Book'} started downloading`, 'info');
        }
      });
      
      // Check for completed items (moved from downloading/queued to available/done)
      const prevDownloadingIds = new Set(Object.keys(prevDownloading));
      const prevQueuedIds = new Set(Object.keys(prevQueued));
      const currAvailable = curr.available || {};
      const currDone = curr.done || {};
      
      Object.keys(currAvailable).forEach((bookId) => {
        if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
          const book = currAvailable[bookId];
          toastNotifications.show(`${book.title || 'Book'} completed`, 'success');
        }
      });
      
      Object.keys(currDone).forEach((bookId) => {
        if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
          const book = currDone[bookId];
          toastNotifications.show(`${book.title || 'Book'} completed`, 'success');
        }
      });
    },
    render(data) {
      // data shape: {queued: {...}, downloading: {...}, completed: {...}, error: {...}}
      const sections = [];
      let hasItems = false;
      for (const [name, items] of Object.entries(data || {})) {
        if (!items || Object.keys(items).length === 0) continue;
        hasItems = true;
        const rows = Object.values(items).map((b) => {
          const titleText = utils.e(b.title) || '-';
          const maybeLinkedTitle = b.download_path
            ? `<a href="/request/api/localdownload?id=${encodeURIComponent(b.id)}" class="text-blue-600 hover:underline">${titleText}</a>`
            : titleText;
          const actions = (name === 'queued' || name === 'downloading')
            ? `<button class="px-2 py-1 rounded border text-xs" data-cancel="${utils.e(b.id)}" style="border-color: var(--border-muted);">Cancel</button>`
            : '';
          const progress = (name === 'downloading' && typeof b.progress === 'number')
            ? `<div class="h-2 bg-black/10 rounded overflow-hidden"><div class="h-2 bg-blue-600" style="width:${Math.round(b.progress)}%"></div></div>`
            : '';
          return `<li class="p-3 rounded border flex flex-col gap-2" style="border-color: var(--border-muted); background: var(--bg-soft)">
            <div class="text-sm"><span class="opacity-70">${utils.e(name)}</span> • <strong>${maybeLinkedTitle}</strong></div>
            ${progress}
            <div class="flex items-center gap-2">${actions}</div>
          </li>`;
        }).join('');
        sections.push(`
          <div>
            <h4 class="font-semibold mb-2">${name.charAt(0).toUpperCase() + name.slice(1)}</h4>
            <ul class="space-y-2">${rows}</ul>
          </div>`);
      }
      el.statusList.innerHTML = sections.join('') || '<div class="text-sm opacity-80">No items.</div>';
      // Show/hide status section based on whether there are items
      if (hasItems) {
        utils.show(el.statusSection);
      } else {
        utils.hide(el.statusSection);
      }
      // Update search section position
      updateSearchSectionPosition();
      // Bind cancel buttons
      el.statusList.querySelectorAll('[data-cancel]')?.forEach((btn) => {
        btn.addEventListener('click', () => queue.cancel(btn.getAttribute('data-cancel')));
      });
    },
    // Render compact active downloads list near the search bar
    renderTop(data) {
      try {
        const downloading = (data && data.downloading) ? Object.values(data.downloading) : [];
        if (!el.activeTopSec || !el.activeTopList) return;
        if (!downloading.length) {
          el.activeTopList.innerHTML = '';
          el.activeTopSec.classList.add('hidden');
          updateSearchSectionPosition();
          return;
        }
        // Build compact rows with title and progress bar + cancel
        const rows = downloading.map((b) => {
          const prog = (typeof b.progress === 'number')
            ? `<div class="h-1.5 bg-black/10 rounded overflow-hidden"><div class="h-1.5 bg-blue-600" style="width:${Math.round(b.progress)}%"></div></div>`
            : '';
          const cancel = `<button class="px-2 py-0.5 rounded border text-xs" data-cancel="${utils.e(b.id)}" style="border-color: var(--border-muted);">Cancel</button>`;
          return `<div class="p-3 rounded border" style="border-color: var(--border-muted); background: var(--bg-soft)">
            <div class="flex items-center justify-between gap-3">
              <div class="text-sm truncate"><strong>${utils.e(b.title || '-') }</strong></div>
              <div class="shrink-0">${cancel}</div>
            </div>
            ${prog}
          </div>`;
        }).join('');
        el.activeTopList.innerHTML = rows;
        el.activeTopSec.classList.remove('hidden');
        // Update search section position
        updateSearchSectionPosition();
        // Bind cancel handlers for the top section
        el.activeTopList.querySelectorAll('[data-cancel]')?.forEach((btn) => {
          btn.addEventListener('click', () => queue.cancel(btn.getAttribute('data-cancel')));
        });
      } catch (_) {}
    },
    async updateActive() {
      try {
        const d = await utils.j(API.activeDownloads);
        const n = Array.isArray(d.active_downloads) ? d.active_downloads.length : 0;
        if (el.activeDownloadsCount) el.activeDownloadsCount.textContent = `Active: ${n}`;
      } catch (_) {}
    }
  };

  // ---- Queue ----
  const queue = {
    async cancel(id) {
      try {
        await fetch(`${API.cancelDownload}/${encodeURIComponent(id)}/cancel`, { method: 'DELETE' });
        status.fetch();
      } catch (_){}
    }
  };
  
  // ---- Toast Notifications ----
  const toastNotifications = {
    container: null,
    init() {
      // Create toast container if it doesn't exist
      if (!this.container) {
        this.container = document.createElement('div');
        this.container.id = 'toast-container';
        this.container.className = 'fixed top-4 right-4 z-50 space-y-2';
        document.body.appendChild(this.container);
      }
    },
    show(message, type = 'info') {
      this.init();
      const toast = document.createElement('div');
      toast.className = `toast-notification px-4 py-3 rounded-md shadow-lg text-sm font-medium transition-all duration-300 ${
        type === 'success' ? 'bg-green-600 text-white' : 'bg-blue-600 text-white'
      }`;
      toast.textContent = message;
      
      // Add to container
      this.container.appendChild(toast);
      
      // Trigger animation
      setTimeout(() => {
        toast.classList.add('toast-visible');
      }, 10);
      
      // Auto-dismiss after 4 seconds
      setTimeout(() => {
        toast.classList.remove('toast-visible');
        setTimeout(() => {
          if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
          }
        }, 300);
      }, 4000);
    }
  };

  // ---- Theme ----
  const theme = {
    KEY: 'preferred-theme',
    init() {
      const saved = localStorage.getItem(this.KEY) || 'auto';
      this.apply(saved);
      this.updateLabel(saved);
      // toggle dropdown
      el.themeToggle?.addEventListener('click', (e) => {
        e.preventDefault();
        if (!el.themeMenu) return;
        el.themeMenu.classList.toggle('hidden');
      });
      // outside click to close
      document.addEventListener('click', (ev) => {
        if (!el.themeMenu || !el.themeToggle) return;
        if (el.themeMenu.contains(ev.target) || el.themeToggle.contains(ev.target)) return;
        el.themeMenu.classList.add('hidden');
      });
      // selection
      el.themeMenu?.querySelectorAll('a[data-theme]')?.forEach((a) => {
        a.addEventListener('click', (ev) => {
          ev.preventDefault();
          const pref = a.getAttribute('data-theme');
          localStorage.setItem(theme.KEY, pref);
          theme.apply(pref);
          theme.updateLabel(pref);
          el.themeMenu.classList.add('hidden');
        });
      });
      // react to system change if auto
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      mq.addEventListener('change', (e) => {
        if ((localStorage.getItem(theme.KEY) || 'auto') === 'auto') {
          document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
        }
      });
    },
    apply(pref) {
      if (pref === 'auto') {
        const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
      } else {
        document.documentElement.setAttribute('data-theme', pref);
      }
    },
    updateLabel(pref) { if (el.themeText) el.themeText.textContent = `Theme (${pref})`; }
  };

  // ---- Wire up ----
  function initEvents() {
    el.searchBtn?.addEventListener('click', () => search.run());
    el.searchInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { search.run(); el.searchInput.blur(); } });

    if (el.advToggle && el.filtersForm) {
      el.advToggle.addEventListener('click', (e) => {
        e.preventDefault();
        el.filtersForm.classList.toggle('hidden');
      });
    }

    el.refreshStatusBtn?.addEventListener('click', () => status.fetch());
    el.activeTopRefreshBtn?.addEventListener('click', () => status.fetch());
    el.clearCompletedBtn?.addEventListener('click', async () => {
      try { await fetch(API.clearCompleted, { method: 'DELETE' }); status.fetch(); } catch (_) {}
    });

    // Close modal on overlay click
    el.modalOverlay?.addEventListener('click', (e) => { if (e.target === el.modalOverlay) modal.close(); });
  }

  // ---- Init ----
  theme.init();
  initEvents();
  toastNotifications.init();
  // Set initial position before first status fetch
  updateSearchSectionPosition();
  status.fetch();
  
  // Auto-update status every 10 seconds
  setInterval(() => {
    status.fetch();
  }, 10000);
})();
