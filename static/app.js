/* 西语 YouTube 学习助手 —— 前端逻辑（原生 JS，无构建步骤）
 *
 * 职责：
 *   1. 调后端接口拿「学习材料」JSON；
 *   2. 渲染单词/短语、句子、段落三块内容；
 *   3. 驱动 YouTube 内嵌播放器，实现「点时间点跳转」；
 *   4. 把所有失败路径显示成用户能看懂的原因 + 可执行的下一步。
 *
 * 播放器策略（诚实降级）：
 *   - 优先用官方 IFrame Player API 的 seekTo()，同一播放器内跳转、不重载；
 *   - API 未就绪 / 被拦 / 报错时，退回「重建带 ?start= 的内嵌地址」，
 *     并明确提示用户「播放器已重新载入」，而不是静默失效。
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------- 元素 --

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    url: $('url'),
    level: $('level'),
    btnGenerate: $('btn-generate'),
    btnDemo: $('btn-demo'),
    btnUpload: $('btn-upload'),
    fileInput: $('file-input'),
    levelNote: $('level-note'),
    banner: $('banner'),
    playerFrame: $('player-frame'),
    playerMeta: $('player-meta'),
    btnOpenYt: $('btn-open-yt'),
    chkFollow: $('chk-follow'),
    statsBody: $('stats-body'),
    warnings: $('warnings'),
    tabCount: $('tab-count'),
    panelVocab: $('panel-vocab'),
    panelSentences: $('panel-sentences'),
    panelParagraphs: $('panel-paragraphs'),
    healthDot: $('health-dot'),
    healthText: $('health-text'),
    toast: $('toast')
  };

  // ---------------------------------------------------------------- 状态 --

  var state = {
    material: null,
    levels: [],          // [{level, label}]
    ytReady: false,
    player: null,        // YT.Player 实例
    usingFallback: false,
    followTimer: null,
    currentSentence: -1,
    activeTab: 'vocab',
    busy: false
  };

  var FALLBACK_LEVEL_NOTE = {
    A1: '入门：只挑最基础的高频词，讲解最精简。',
    A2: '初级：开始出现常用搭配与简单时态说明。',
    B1: '中级：补充固定搭配与介词用法，讲解时态变化。',
    B2: '中高级：强调搭配、语域与近义辨析。',
    C1: '高级：关注习语、隐含语气与文体差异。',
    C2: '精通：只列出最生僻/超出 C2 的词，给出语域与修辞提示。'
  };

  // --------------------------------------------------------------- 小工具 --

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtTime(seconds) {
    var s = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return h > 0 ? h + ':' + pad(m) + ':' + pad(sec) : pad(m) + ':' + pad(sec);
  }

  var toastTimer = null;
  function toast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(function () { el.toast.hidden = true; }, 4200);
  }

  function setBusy(busy, label) {
    state.busy = busy;
    el.btnGenerate.disabled = busy;
    el.btnDemo.disabled = busy;
    el.btnUpload.disabled = busy;
    el.btnGenerate.innerHTML = busy
      ? '<span class="spinner"></span>' + esc(label || '处理中…')
      : '生成学习材料';
  }

  function showBanner(kind, title, hint, extraHtml) {
    el.banner.className = 'banner' + (kind === 'info' ? ' info' : '');
    el.banner.innerHTML =
      '<span class="b-icon">' + (kind === 'info' ? 'i' : '!') + '</span>' +
      '<div class="b-body">' +
        '<div class="b-title">' + esc(title) + '</div>' +
        (hint ? '<div class="b-hint">' + esc(hint) + '</div>' : '') +
        (extraHtml || '') +
        '<div class="b-actions">' +
          '<button type="button" class="btn" data-action="demo">载入示例</button>' +
          '<button type="button" class="btn" data-action="upload">导入字幕文件</button>' +
        '</div>' +
      '</div>';
    el.banner.hidden = false;
  }

  function hideBanner() {
    el.banner.hidden = true;
    el.banner.innerHTML = '';
  }

  function chip(text, cls) {
    return '<span class="chip ' + (cls || '') + '">' + esc(text) + '</span>';
  }

  // --------------------------------------------------------------- 播放器 --

  function ytSrc(video, startSeconds) {
    var params = [
      'rel=0',
      'enablejsapi=1',
      'playsinline=1',
      'origin=' + encodeURIComponent(window.location.origin),
      'start=' + Math.max(0, Math.floor(startSeconds || 0))
    ];
    return 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(video.video_id) +
      '?' + params.join('&');
  }

  function loadYtApi() {
    if (window.YT && window.YT.Player) {
      state.ytReady = true;
      if (state.material) { mountPlayer(state.material.video); }
      return;
    }
    if (document.querySelector('script[data-yt-api]')) { return; }

    var previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = function () {
      if (typeof previous === 'function') { previous(); }
      state.ytReady = true;
      if (state.material) { mountPlayer(state.material.video); }
    };

    var script = document.createElement('script');
    script.src = 'https://www.youtube.com/iframe_api';
    script.async = true;
    script.setAttribute('data-yt-api', '1');
    document.head.appendChild(script);
  }

  function clearPlayerSurface() {
    if (state.player && typeof state.player.destroy === 'function') {
      try { state.player.destroy(); } catch (e) { /* 忽略销毁异常 */ }
    }
    state.player = null;
    state.usingFallback = false;
    el.playerFrame.innerHTML = '';
  }

  function idlePlayer(title, sub) {
    el.playerFrame.innerHTML =
      '<div class="player-idle">' +
        '<p class="player-idle-title">' + esc(title) + '</p>' +
        '<p class="player-idle-sub">' + esc(sub) + '</p>' +
      '</div>';
  }

  function mountFallbackIframe(video, startSeconds) {
    clearPlayerSurface();
    var iframe = document.createElement('iframe');
    iframe.src = ytSrc(video, startSeconds);
    iframe.title = 'YouTube 播放器';
    iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
    iframe.setAttribute('allowfullscreen', '');
    iframe.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    el.playerFrame.appendChild(iframe);
    state.usingFallback = true;
  }

  function mountYtPlayer(video) {
    clearPlayerSurface();

    var host = document.createElement('div');
    host.id = 'yt-host';
    host.style.width = '100%';
    host.style.height = '100%';
    el.playerFrame.appendChild(host);

    try {
      state.player = new window.YT.Player(host, {
        videoId: video.video_id,
        width: '100%',
        height: '100%',
        playerVars: {
          rel: 0,
          enablejsapi: 1,
          playsinline: 1,
          origin: window.location.origin,
          start: Math.max(0, Math.floor(video.start_seconds || 0))
        },
        events: {
          onError: function () {
            // 例如视频禁止内嵌：退回普通内嵌地址，并明确告知用户。
            // 延后一拍再销毁，避免在接口自身的回调里同步拆掉播放器。
            setTimeout(function () {
              var fallbackVideo = (state.material && state.material.video) || video;
              mountFallbackIframe(fallbackVideo, fallbackVideo.start_seconds);
              toast('播放器接口不可用，已改用基础内嵌播放器（时间跳转会重新载入视频）。');
            }, 0);
          }
        }
      });
      state.usingFallback = false;
    } catch (e) {
      mountFallbackIframe(video, video.start_seconds);
    }
  }

  function mountPlayer(video) {
    if (!video || !video.has_player || !video.video_id) {
      clearPlayerSurface();
      idlePlayer(
        '没有可播放的视频',
        video && video.is_demo
          ? '这是内置示例材料，本身不含视频；导入带链接的字幕或粘贴 YouTube 链接即可看到播放器。'
          : '当前材料没有关联视频，学习内容与时间点仍然可用。'
      );
      return;
    }

    if (state.ytReady && window.YT && window.YT.Player) {
      mountYtPlayer(video);
    } else {
      mountFallbackIframe(video, video.start_seconds);
      loadYtApi();
    }
  }

  function seekTo(seconds) {
    var video = state.material && state.material.video;
    if (!video || !video.has_player) {
      toast('这段材料没有可跳转的视频。');
      return;
    }
    var target = Math.max(0, Math.floor(Number(seconds) || 0));

    if (state.player && typeof state.player.seekTo === 'function') {
      try {
        state.player.seekTo(target, true);
        if (typeof state.player.playVideo === 'function') { state.player.playVideo(); }
        return;
      } catch (e) { /* 落到下面的降级路径 */ }
    }

    // 回退：重建内嵌地址（?start=…&autoplay=1）
    clearPlayerSurface();
    var iframe = document.createElement('iframe');
    iframe.src = ytSrc(video, target) + '&autoplay=1';
    iframe.title = 'YouTube 播放器';
    iframe.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
    iframe.setAttribute('allowfullscreen', '');
    iframe.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    el.playerFrame.appendChild(iframe);
    state.usingFallback = true;
    toast('播放器未就绪，已重新载入到 ' + fmtTime(target) + '。');
  }

  // -------------------------------------------------------- 跟随播放高亮 --

  function setCurrentSentence(index) {
    if (index === state.currentSentence) { return; }
    state.currentSentence = index;

    var items = el.panelSentences.querySelectorAll('[data-sent-index]');
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var isCurrent = Number(item.getAttribute('data-sent-index')) === index;
      item.classList.toggle('is-current', isCurrent);
      if (isCurrent) {
        var rect = item.getBoundingClientRect();
        var visible = rect.top >= 0 && rect.bottom <= (window.innerHeight || 800);
        if (!visible) { item.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
      }
    }
  }

  function tickFollow() {
    if (!state.player || typeof state.player.getCurrentTime !== 'function') { return; }
    var t;
    try { t = state.player.getCurrentTime(); } catch (e) { return; }
    if (typeof t !== 'number' || isNaN(t)) { return; }

    var segs = (state.material && state.material.segments) || [];
    var found = -1;
    for (var i = 0; i < segs.length; i++) {
      if (t >= segs[i].start - 0.3 && t < segs[i].end + 0.6) { found = i; break; }
    }
    if (found === -1) {
      for (var j = segs.length - 1; j >= 0; j--) {
        if (segs[j].end <= t) { found = j; break; }
      }
    }
    setCurrentSentence(found);
  }

  function stopFollow() {
    if (state.followTimer) { clearInterval(state.followTimer); state.followTimer = null; }
    setCurrentSentence(-1);
  }

  function startFollow() {
    stopFollow();
    if (state.activeTab !== 'sentences') { activateTab('sentences'); }
    state.followTimer = setInterval(tickFollow, 700);
    tickFollow();
  }

  // ----------------------------------------------------------------- 标签 --

  function activateTab(name) {
    state.activeTab = name;
    var tabs = document.querySelectorAll('.tab');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === name);
    }
    el.panelVocab.hidden = name !== 'vocab';
    el.panelSentences.hidden = name !== 'sentences';
    el.panelParagraphs.hidden = name !== 'paragraphs';

    if (!state.material) { return; }
    if (name === 'vocab') {
      el.tabCount.textContent = state.material.vocabulary.length + ' 个词条';
    } else if (name === 'sentences') {
      el.tabCount.textContent = state.material.segments.length + ' 个句子';
    } else {
      el.tabCount.textContent = state.material.paragraphs.length + ' 个段落';
    }
  }

  // --------------------------------------------------------------- 渲染 --

  function tsButton(seconds, label, enabled) {
    if (!enabled) {
      return '<button type="button" class="ts-btn no-seek" disabled title="该材料没有可跳转的视频">' +
        '<span class="tri">&#9654;</span>' + esc(label) + '</button>';
    }
    return '<button type="button" class="ts-btn" data-seek="' + esc(seconds) + '" ' +
      'title="跳转到 ' + esc(label) + '">' +
      '<span class="tri">&#9654;</span>' + esc(label) + '</button>';
  }

  function renderLevelNote() {
    var level = el.level.value;
    var fromApi = null;
    for (var i = 0; i < state.levels.length; i++) {
      if (state.levels[i].level === level) { fromApi = state.levels[i].label; break; }
    }
    var text = fromApi || FALLBACK_LEVEL_NOTE[level] || '';
    if (state.material && state.material.level === level && state.material.level_profile) {
      var p = state.material.level_profile;
      text = (p.label || text) +
        ' 词汇区间 ' + p.band.join('–') + '，最多列出 ' + p.max_vocab + ' 个词条。';
    }
    el.levelNote.textContent = text;
  }

  function renderPlayerMeta(material) {
    var v = material.video || {};
    var parts = [];
    if (v.is_demo) { parts.push(chip('内置示例材料', 'soft')); }
    else if (material.source === 'youtube') { parts.push(chip('YouTube 字幕', 'soft')); }
    else if (material.source === 'import') { parts.push(chip('导入的字幕', 'soft')); }

    if (v.language) { parts.push(chip(v.language + (v.language_code ? ' · ' + v.language_code : ''), 'outline')); }
    if (v.subtitle_origin) { parts.push(chip(v.subtitle_origin, 'outline')); }
    if (v.is_generated) { parts.push(chip('自动生成，可能有识别误差', 'lv-C1')); }
    if (v.subtitle_filename) { parts.push(chip(v.subtitle_filename, 'outline')); }
    if (v.start_seconds) { parts.push(chip('起点 ' + fmtTime(v.start_seconds), 'outline')); }
    el.playerMeta.innerHTML = parts.join('') || '<span class="muted">—</span>';

    var hasVideo = Boolean(v.has_player && v.video_id);
    el.btnOpenYt.disabled = !hasVideo;
    el.btnOpenYt.setAttribute('data-url', hasVideo ? v.watch_url : '');
    el.chkFollow.disabled = !hasVideo;
    if (!hasVideo) { el.chkFollow.checked = false; stopFollow(); }
  }

  function renderStats(material) {
    var s = material.stats || {};
    var stats = [
      { n: s.cues, l: '字幕条数' },
      { n: s.sentences, l: '句子' },
      { n: s.paragraphs, l: '段落' },
      { n: s.vocabulary, l: '列出词汇' },
      { n: s.vocabulary_with_meaning, l: '含中文释义' },
      { n: s.sentences_translated, l: '有译文句子' }
    ];

    var html = '<div class="stats-grid">';
    for (var i = 0; i < stats.length; i++) {
      html += '<div class="stat"><div class="stat-n">' + esc(stats[i].n === undefined ? '–' : stats[i].n) +
        '</div><div class="stat-l">' + esc(stats[i].l) + '</div></div>';
    }
    html += '</div>';

    var meta = material.meta || {};
    var levelData = meta.level_data || {};
    var glossary = meta.glossary || {};
    var translator = meta.translator || {};

    var glossaryLabel;
    if (glossary.sources && glossary.sources.length) {
      glossaryLabel = glossary.sources
        .map(function (s) { return (s.label || s.name) + ' ' + s.entries + ' 条'; })
        .join(' + ');
    } else {
      glossaryLabel = glossary.source === 'wiktionary'
        ? 'Wiktionary 西汉词表（' + glossary.entries + ' 条）'
        : (glossary.source === 'core'
            ? '内置精编词表（' + glossary.entries + ' 条，MIT）'
            : '未加载到词表');
    }

    var levelLabel = levelData.authoritative_available
      ? 'ELELex 权威标注（' + levelData.authoritative_entries + ' 条）+ wordfreq 词频近似'
      : '仅 wordfreq 词频近似（未找到 ELELex 数据）';

    var translatorModel = translator.status && translator.status.model
      ? '（模型：' + translator.status.model + '）' : '';
    html += '<dl class="kv">' +
      '<div><dt>材料来源</dt><dd>' + esc(sourceLabel(material.source)) + '</dd></div>' +
      '<div><dt>中文释义</dt><dd>' + esc(glossaryLabel) + '</dd></div>' +
      '<div><dt>等级判定</dt><dd>' + esc(levelLabel) + '</dd></div>' +
      '<div><dt>翻译方式</dt><dd>' + esc((translator.note || '—') + translatorModel) + '</dd></div>' +
      '</dl>';

    if (material.vocabulary_hint) {
      html += '<div class="vocab-hint">' + esc(material.vocabulary_hint) + '</div>';
    }

    el.statsBody.innerHTML = html;

    var warnings = material.warnings || [];
    if (warnings.length) {
      var list = '';
      for (var w = 0; w < warnings.length; w++) { list += '<li>' + esc(warnings[w]) + '</li>'; }
      el.warnings.innerHTML = '<h3>需要留意的地方</h3><ul>' + list + '</ul>';
      el.warnings.hidden = false;
    } else {
      el.warnings.hidden = true;
      el.warnings.innerHTML = '';
    }
  }

  function sourceLabel(source) {
    if (source === 'youtube') { return 'YouTube 字幕（自动抓取）'; }
    if (source === 'import') { return '本地导入的字幕文件'; }
    if (source === 'demo') { return '内置示例字幕（自撰，无网络请求）'; }
    return source || '—';
  }

  function renderVocab(material) {
    var items = material.vocabulary || [];
    var canSeek = Boolean(material.video && material.video.has_player);

    if (!items.length) {
      el.panelVocab.innerHTML =
        '<p class="muted">按当前等级（' + esc(material.level) + '）筛选后没有剩余生词。' +
        '可以把等级调低一档，或换一段材料试试。</p>';
      return;
    }

    var html = '';
    if (material.vocabulary_hint) {
      html += '<div class="vocab-hint">' + esc(material.vocabulary_hint) + '</div>';
    }
    html += '<div class="vocab-list">';

    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var levelCls = 'lv-' + (it.level || 'X');
      var srcCls = it.level_source === 'elelex' ? 'src-elelex' : (it.level_source === 'wordfreq' ? 'src-word' : '');
      var srcLabel = it.level_source === 'elelex' ? 'ELELex'
        : (it.level_source === 'wordfreq' ? '词频近似' : '未知来源');

      var meanings = it.meaning_zh || [];
      var meaningsHtml;
      if (meanings.length === 1) {
        meaningsHtml = '<div class="vocab-meanings">' + esc(meanings[0]) + '</div>';
      } else if (meanings.length > 1) {
        meaningsHtml = '<div class="vocab-meanings"><ol>';
        for (var m = 0; m < meanings.length; m++) {
          meaningsHtml += '<li>' + esc(meanings[m]) + '</li>';
        }
        meaningsHtml += '</ol></div>';
      } else {
        meaningsHtml = '<div class="vocab-meanings"><span class="vocab-missing">未收录中文释义' +
          '（可运行 tools/build_glossary.py 生成更完整的词表，或配置大模型补全）</span></div>';
      }

      var noteHtml = it.meaning_note
        ? '<div class="vocab-note">' + esc(it.meaning_note) + '</div>'
        : '';

      var surfaceHtml = (it.surface && it.surface !== it.term.toLowerCase())
        ? '<span class="vocab-surface">原文形态 ' + esc(it.surface) + '</span>'
        : '';

      html +=
        '<div class="vocab-item" data-vocab-key="' + esc(it.key) + '">' +
          '<div class="vocab-main">' +
            '<div class="vocab-head">' +
              '<span class="vocab-term">' + esc(it.term) + '</span>' +
              (it.pos ? chip(it.pos, 'outline') : '') +
              surfaceHtml +
            '</div>' +
            '<div class="vocab-meta">' +
              chip(it.level || '?', levelCls) +
              chip(srcLabel, srcCls) +
              (it.zipf ? chip('词频 ' + it.zipf, 'soft') : '') +
            '</div>' +
            meaningsHtml + noteHtml +
          '</div>' +
          '<div class="vocab-side">' +
            tsButton(it.timestamp, it.timestamp_label, canSeek) +
            '<span class="vocab-count">出现 ' + esc(it.count) + ' 次</span>' +
          '</div>' +
        '</div>';
    }

    html += '</div>';
    el.panelVocab.innerHTML = html;
  }

  function renderSentences(material) {
    var segs = material.segments || [];
    var canSeek = Boolean(material.video && material.video.has_player);

    if (!segs.length) {
      el.panelSentences.innerHTML = '<p class="muted">没有解析出句子。</p>';
      return;
    }

    var html = '<div class="sent-list">';
    for (var i = 0; i < segs.length; i++) {
      var seg = segs[i];

      var zh = seg.translation_zh
        ? '<div class="sent-zh">' + esc(seg.translation_zh) +
            (seg.translation_approximate && seg.translation_source !== 'authored' ? '<span class="approx">近似直译</span>' : '') +
            (seg.translation_source === 'authored' ? '<span class="src">人工译文</span>' : '') +
            (seg.translation_source && seg.translation_source.indexOf('llm') === 0 ? '<span class="src ai">AI 译文</span>' : '') +
            (seg.translation_source === 'mymemory-free' ? '<span class="src mt">机翻译文</span>' : '') +
          '</div>'
        : '<div class="sent-zh"><span class="muted">（暂无中文译文）</span></div>';

      var glossHtml = '';
      if (seg.glosses && seg.glosses.length) {
        glossHtml = '<div class="gloss-row">';
        for (var g = 0; g < seg.glosses.length; g++) {
          var gl = seg.glosses[g];
          if (gl.zh) {
            glossHtml += '<span class="gloss"><b>' + esc(gl.surface) + '</b>' +
              '<span class="arrow">&rarr;</span>' + esc(gl.zh) + '</span>';
          } else {
            glossHtml += '<span class="gloss miss">' + esc(gl.surface) + '</span>';
          }
        }
        glossHtml += '</div>';
      }

      var notesHtml = '';
      if (seg.notes && seg.notes.length) {
        notesHtml = '<div class="notes"><ul>';
        for (var n = 0; n < seg.notes.length; n++) {
          notesHtml += '<li>' + esc(seg.notes[n]) + '</li>';
        }
        notesHtml += '</ul></div>';
      }

      html +=
        '<div class="sent-item" data-sent-index="' + esc(seg.index) + '" data-start="' + esc(seg.start) + '">' +
          '<div class="sent-head">' +
            '<span class="sent-no">#' + esc(seg.index + 1) + '</span>' +
            tsButton(seg.start, seg.start_label, canSeek) +
          '</div>' +
          '<div class="sent-es">' + esc(seg.text_es) + '</div>' +
          zh + glossHtml + notesHtml +
        '</div>';
    }
    html += '</div>';
    el.panelSentences.innerHTML = html;
  }

  function renderParagraphs(material) {
    var paras = material.paragraphs || [];
    var canSeek = Boolean(material.video && material.video.has_player);

    if (!paras.length) {
      el.panelParagraphs.innerHTML = '<p class="muted">没有解析出段落。</p>';
      return;
    }

    var html = '<div class="para-list">';
    for (var i = 0; i < paras.length; i++) {
      var p = paras[i];
      var links = '';
      if (p.sentence_indexes && p.sentence_indexes.length) {
        links = '<div class="para-sent-links">';
        for (var k = 0; k < p.sentence_indexes.length; k++) {
          var si = p.sentence_indexes[k];
          links += '<button type="button" data-goto-sent="' + esc(si) + '">句 #' + esc(si + 1) + '</button>';
        }
        links += '</div>';
      }

      html +=
        '<div class="para-item">' +
          '<div class="para-head">' +
            '<span class="sent-no">段落 ' + esc(p.index + 1) + '</span>' +
            tsButton(p.start, p.start_label, canSeek) +
            chip('含 ' + (p.sentence_indexes ? p.sentence_indexes.length : 0) + ' 句', 'soft') +
          '</div>' +
          '<div class="para-es">' + esc(p.text_es) + '</div>' +
          links +
        '</div>';
    }
    html += '</div>';
    el.panelParagraphs.innerHTML = html;
  }

  function renderAll(material) {
    state.material = material;
    state.currentSentence = -1;

    hideBanner();
    renderPlayerMeta(material);
    mountPlayer(material.video || {});
    renderStats(material);
    renderVocab(material);
    renderSentences(material);
    renderParagraphs(material);
    renderLevelNote();
    activateTab(state.activeTab);
  }

  // --------------------------------------------------------------- 请求 --

  function handleApiError(payload, status) {
    var code = payload.error_code || 'unknown';
    var extra = '';

    if (payload.available_languages && payload.available_languages.length) {
      var list = '';
      for (var i = 0; i < payload.available_languages.length && i < 12; i++) {
        var l = payload.available_languages[i];
        list += '<li>' + esc((l.name || l.code || '未知') + '（' + (l.generated ? '自动' : '人工') + '）') + '</li>';
      }
      extra = '<div style="margin-top:6px">该视频实际可用的字幕：</div><ul>' + list + '</ul>';
    }

    showBanner(
      'error',
      '无法生成学习材料（' + code + (status ? ' / HTTP ' + status : '') + '）',
      payload.message || '未知错误',
      (payload.hint ? '<div class="b-hint">建议：' + esc(payload.hint) + '</div>' : '') + extra
    );
  }

  function loadDemo() {
    if (state.busy) { return; }
    setBusy(true, '载入示例…');
    hideBanner();

    fetch('/api/demo?level=' + encodeURIComponent(el.level.value))
      .then(function (res) { return res.json().then(function (body) { return { res: res, body: body }; }); })
      .then(function (r) {
        if (!r.res.ok || r.body.error) { handleApiError(r.body, r.res.status); return; }
        renderAll(r.body);
      })
      .catch(function (err) {
        showBanner('error', '无法载入示例', String(err && err.message ? err.message : err));
      })
      .finally(function () { setBusy(false); });
  }

  function generate() {
    if (state.busy) { return; }
    var url = (el.url.value || '').trim();
    if (!url) {
      showBanner('error', '请先填写 YouTube 链接', '例如默认的测试链接。');
      el.url.focus();
      return;
    }

    setBusy(true, '抓取字幕中…');
    hideBanner();
    // LLM 整句翻译可能较慢：8 秒后仍未完成就升级提示文案
    var slowTimer = setTimeout(function () {
      if (state.busy) { setBusy(true, 'AI 翻译中，长视频约需 1-2 分钟…'); }
    }, 8000);

    fetch('/api/material', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url, level: el.level.value })
    })
      .then(function (res) { return res.json().then(function (body) { return { res: res, body: body }; }); })
      .then(function (r) {
        if (!r.res.ok || r.body.error) { handleApiError(r.body, r.res.status); return; }
        renderAll(r.body);
        if (r.body.video && r.body.video.is_generated) {
          toast('注意：该视频使用的是自动生成字幕，可能存在识别误差。');
        }
      })
      .catch(function (err) {
        showBanner('error', '请求失败', String(err && err.message ? err.message : err),
          '<div class="b-hint">请确认本地服务仍在运行，或改用「载入示例」。</div>');
      })
      .finally(function () { clearTimeout(slowTimer); setBusy(false); });
  }

  function upload(file) {
    if (!file) { return; }
    if (state.busy) { return; }

    var name = file.name || '';
    var lower = name.toLowerCase();
    if (!/\.(srt|vtt|txt)$/.test(lower)) {
      showBanner('error', '不支持的字幕格式',
        '请上传 .srt 或 .vtt 文件。若你的字幕是 .txt，请先转换格式。');
      return;
    }

    var form = new FormData();
    form.append('subtitle', file);
    form.append('level', el.level.value);
    form.append('url', (el.url.value || '').trim());

    setBusy(true, '解析字幕中…');
    hideBanner();

    fetch('/api/material/upload', { method: 'POST', body: form })
      .then(function (res) { return res.json().then(function (body) { return { res: res, body: body }; }); })
      .then(function (r) {
        if (!r.res.ok || r.body.error) { handleApiError(r.body, r.res.status); return; }
        renderAll(r.body);
        toast('已从「' + name + '」整理出学习材料。');
      })
      .catch(function (err) {
        showBanner('error', '上传失败', String(err && err.message ? err.message : err));
      })
      .finally(function () { setBusy(false); el.fileInput.value = ''; });
  }

  function loadHealth() {
    fetch('/api/health')
      .then(function (res) { return res.json(); })
      .then(function (h) {
        state.levels = h.levels || [];
        renderLevelNote();

        var g = h.glossary || {};
        var ld = h.level_data || {};
        var srcText = (g.sources && g.sources.length)
          ? g.sources.map(function (s) { return (s.label || s.name) + ' ' + s.entries; }).join(' + ')
          : (g.source === 'wiktionary' ? 'Wiktionary' : (g.source === 'core' ? '内置精编' : '未加载'));
        var bits = [];
        bits.push('词表 ' + (g.entries || 0) + ' 条（' + srcText + '）');
        bits.push(ld.available ? 'ELELex 已加载' : 'ELELex 未找到');
        bits.push((h.llm && h.llm.configured) ? '大模型已配置' : '大模型未配置（离线模式）');
        el.healthText.textContent = bits.join(' · ');

        var dot = 'ok';
        if (!g.entries) { dot = 'err'; }
        else if (!ld.available || !(h.llm && h.llm.configured)) { dot = 'warn'; }
        el.healthDot.className = 'health-dot ' + dot;

        var problems = [];
        if (g.warnings && g.warnings.length) { problems = problems.concat(g.warnings); }
        if (!ld.available) { problems.push('未找到 ELELex 等级数据，等级判定将完全依赖词频近似。'); }
        if (problems.length) {
          showBanner('info', '运行状态提示', problems.join(' '));
        }
      })
      .catch(function () {
        el.healthDot.className = 'health-dot err';
        el.healthText.textContent = '无法读取运行状态';
      });
  }

  // --------------------------------------------------------------- 事件 --

  document.addEventListener('click', function (event) {
    var seekBtn = event.target.closest ? event.target.closest('[data-seek]') : null;
    if (seekBtn) {
      seekTo(Number(seekBtn.getAttribute('data-seek')));
      return;
    }

    var gotoSent = event.target.closest ? event.target.closest('[data-goto-sent]') : null;
    if (gotoSent) {
      activateTab('sentences');
      var idx = Number(gotoSent.getAttribute('data-goto-sent'));
      var target = el.panelSentences.querySelector('[data-sent-index="' + idx + '"]');
      if (target) {
        target.scrollIntoView({ block: 'center', behavior: 'smooth' });
        target.classList.add('is-current');
        setTimeout(function () { target.classList.remove('is-current'); }, 1600);
      }
      return;
    }

    var tab = event.target.closest ? event.target.closest('.tab') : null;
    if (tab) {
      activateTab(tab.getAttribute('data-tab'));
      return;
    }

    var action = event.target.closest ? event.target.closest('[data-action]') : null;
    if (action) {
      var what = action.getAttribute('data-action');
      if (what === 'demo') { loadDemo(); }
      if (what === 'upload') { el.fileInput.click(); }
      return;
    }

    if (event.target === el.btnOpenYt) {
      var url = el.btnOpenYt.getAttribute('data-url');
      if (url) { window.open(url, '_blank', 'noopener'); }
    }
  });

  el.btnGenerate.addEventListener('click', generate);
  el.btnDemo.addEventListener('click', loadDemo);
  el.btnUpload.addEventListener('click', function () { el.fileInput.click(); });
  el.fileInput.addEventListener('change', function (e) {
    var file = e.target.files && e.target.files[0];
    if (file) { upload(file); }
  });

  el.level.addEventListener('change', function () {
    renderLevelNote();
    // 已经生成过材料时，切档立即按新等级重新整理，避免"看起来没反应"
    if (state.material) {
      if (state.material.source === 'demo') { loadDemo(); }
      else if (state.material.source === 'youtube' && (el.url.value || '').trim()) { generate(); }
    }
  });

  el.url.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); generate(); }
  });

  el.chkFollow.addEventListener('change', function () {
    if (el.chkFollow.checked) {
      if (!state.player) {
        toast('播放器尚未就绪，暂时无法跟随播放。');
        el.chkFollow.checked = false;
        return;
      }
      startFollow();
    } else {
      stopFollow();
    }
  });

  // 页面切到后台时暂停轮询，回到前台且仍勾选时恢复
  document.addEventListener('visibilitychange', function () {
    if (!el.chkFollow.checked) { return; }
    if (document.hidden) {
      if (state.followTimer) { clearInterval(state.followTimer); state.followTimer = null; }
    } else if (!state.followTimer) {
      state.followTimer = setInterval(tickFollow, 700);
    }
  });

  // ---------------------------------------------------------------- 启动 --

  loadHealth();
  renderLevelNote();
  setBusy(false);
  loadYtApi();
})();
