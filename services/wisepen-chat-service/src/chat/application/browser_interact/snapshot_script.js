() => {
  const elements = [];
  let idx = 0;

  const REF_ATTR = 'data-agent-ref';

  const skip = new Set([
    'SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'HEAD', 'META', 'LINK',
    'PATH', 'CIRCLE', 'RECT', 'POLYGON', 'USE', 'DEFS', 'G', 'BR', 'HR'
  ]);

  const containerRoles = new Set([
    'banner', 'navigation', 'main', 'contentinfo', 'complementary',
    'region', 'form', 'group', 'list', 'listitem'
  ]);

  const actionRoles = new Set([
    'button', 'link', 'tab', 'menuitem', 'option',
    'checkbox', 'radio', 'combobox'
  ]);

  document
    .querySelectorAll('[' + REF_ATTR + ']')
    .forEach(el => el.removeAttribute(REF_ATTR));

  function isVisible(el) {
    const style = window.getComputedStyle(el);

    if (style.display === 'none') return false;
    if (style.visibility === 'hidden') return false;
    if (style.opacity === '0') return false;

    const rect = el.getBoundingClientRect();

    if (rect.width === 0 && rect.height === 0) return false;

    return true;
  }

  function isDisabled(el) {
    return Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true';
  }

  function getRectInfo(el) {
    const rect = el.getBoundingClientRect();

    const inViewport =
      rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= window.innerHeight &&
      rect.left <= window.innerWidth;

    return {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      inViewport
    };
  }

  function isClickable(el, role) {
    if (isDisabled(el)) return false;

    const style = window.getComputedStyle(el);
    if (style.pointerEvents === 'none') return false;

    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();

    if (tag === 'a' && el.href) return true;
    if (tag === 'button') return true;

    if (tag === 'input') {
      return ['submit', 'button', 'reset', 'image'].includes(type);
    }

    if (['button', 'link', 'tab', 'menuitem', 'option'].includes(role)) {
      return true;
    }

    if (typeof el.onclick === 'function') return true;
    if (style.cursor === 'pointer') return true;

    return false;
  }

  function isFillable(el) {
    if (isDisabled(el)) return false;

    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'textbox' || role === 'searchbox') return true;

    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();

    if (tag === 'textarea') return true;

    if (tag === 'input') {
      return ![
        'hidden',
        'submit',
        'button',
        'checkbox',
        'radio',
        'image',
        'file',
        'reset',
        'color',
        'range'
      ].includes(type);
    }

    if (el.isContentEditable) return true;

    return false;
  }

  function getRole(el) {
    const explicitRole = el.getAttribute('role') || '';
    if (explicitRole) return explicitRole;

    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();

    if (el.isContentEditable) return 'textbox';

    if (tag === 'input') {
      if (type === 'hidden') return '';

      if (
        type === 'submit' ||
        type === 'button' ||
        type === 'reset' ||
        type === 'image'
      ) {
        return 'button';
      }

      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';

      return 'textbox';
    }

    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.href) return 'link';
    if (tag === 'iframe') return 'iframe';
    if (tag === 'img') return 'img';
    if (tag === 'video') return 'video';

    return '';
  }

  function getLabel(el, role, fillable) {
    const direct =
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      el.getAttribute('name') ||
      '';

    if (direct) {
      return direct.trim().slice(0, 80);
    }

    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy
        .split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(labelEl => (labelEl.innerText || labelEl.textContent || '').trim())
        .filter(Boolean);

      if (parts.length > 0) {
        return parts.join(' ').slice(0, 80);
      }
    }

    const describedBy = el.getAttribute('aria-describedby');
    if (describedBy) {
      const parts = describedBy
        .split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(labelEl => (labelEl.innerText || labelEl.textContent || '').trim())
        .filter(Boolean);

      if (parts.length > 0) {
        return parts.join(' ').slice(0, 80);
      }
    }

    if (el.id) {
      const labelEl = document.querySelector(
        'label[for="' + CSS.escape(el.id) + '"]'
      );

      if (labelEl) {
        const text = (
          labelEl.innerText ||
          labelEl.textContent ||
          ''
        ).trim().slice(0, 80);

        if (text) return text;
      }
    }

    const parentLabel = el.closest('label');
    if (parentLabel) {
      const text = (
        parentLabel.innerText ||
        parentLabel.textContent ||
        ''
      ).trim().slice(0, 80);

      if (text) return text;
    }

    if (fillable) {
      const nearbyText = getNearbyText(el);
      if (nearbyText) {
        return nearbyText;
      }
    }

    const selfText = (
      el.innerText ||
      el.textContent ||
      ''
    ).trim().slice(0, 80);

    return selfText;
  }

  function cleanText(value) {
    return (value || '').replace(/\s+/g, ' ').trim();
  }

  function getElementText(el) {
    return cleanText(el.innerText || el.textContent || '').slice(0, 80);
  }

  function getNearbyText(el) {
    const previous = el.previousElementSibling;
    if (previous) {
      const previousText = getElementText(previous);
      if (previousText) return previousText;
    }

    const parent = el.parentElement;
    if (parent) {
      const label = parent.querySelector('label');
      if (label && !label.contains(el)) {
        const labelText = getElementText(label);
        if (labelText) return labelText;
      }

      const parentPrevious = parent.previousElementSibling;
      if (parentPrevious) {
        const parentPreviousText = getElementText(parentPrevious);
        if (parentPreviousText) return parentPreviousText;
      }
    }

    const fieldContainer = el.closest(
      '.form-group,.field,.form-field,.input-group,.control,.row,[class*="field"],[class*="form"]'
    );
    if (fieldContainer) {
      const text = getElementText(fieldContainer);
      if (text) return text;
    }

    return '';
  }

  function getAncestorText(el) {
    const parts = [];
    let current = el.parentElement;

    while (current && parts.length < 3) {
      const role = current.getAttribute('role') || '';
      const aria = current.getAttribute('aria-label') || '';
      const title = current.getAttribute('title') || '';
      const text = (
        aria ||
        title ||
        current.querySelector('label')?.innerText ||
        ''
      ).trim();

      if (role) parts.push(role);
      if (text) parts.push(text.slice(0, 80));

      current = current.parentElement;
    }

    return parts.join(' ').slice(0, 160);
  }

  function shouldExpose(el, role, visible, clickable, fillable) {
    if (!role || !visible) return false;

    if (role === 'iframe') return true;
    if (fillable) return true;

    if (containerRoles.has(role)) return false;

    if (clickable && actionRoles.has(role)) return true;

    return false;
  }

  function buildFlags(fillable, clickable, role) {
    const flags = [];

    if (fillable) {
      flags.push('fillable');
    }

    if (!clickable && !fillable && role !== 'iframe') {
      flags.push('not-clickable');
    }

    if (role === 'iframe') {
      flags.push('frame');
    }

    return flags;
  }

  function walk(root, depth) {
    const children = root.shadowRoot ? root.shadowRoot.children : root.children;

    if (!children) return;

    for (const el of children) {
      if (!el || !el.tagName || skip.has(el.tagName)) {
        continue;
      }

      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();

      if (tag === 'input' && type === 'hidden') continue;

      const role = getRole(el);
      const visible = isVisible(el);
      const clickable = visible ? isClickable(el, role) : false;
      const fillable = visible ? isFillable(el) : false;

      if (shouldExpose(el, role, visible, clickable, fillable)) {
        idx += 1;

        const ref = 'e' + idx;

        el.setAttribute(REF_ATTR, ref);

        const label = getLabel(el, role, fillable);
        const flags = buildFlags(fillable, clickable, role);
        const rectInfo = getRectInfo(el);

        elements.push({
          ref: ref,
          role: role,
          label: label,
          flags: flags,
          tag: tag,
          type: type,
          id: el.id || '',
          name: el.getAttribute('name') || '',
          placeholder: el.getAttribute('placeholder') || '',
          ariaLabel: el.getAttribute('aria-label') || '',
          title: el.getAttribute('title') || '',
          className: String(el.className || '').slice(0, 160),
          ancestorText: getAncestorText(el),
          fillable: fillable,
          clickable: clickable,
          inViewport: rectInfo.inViewport,
          x: rectInfo.x,
          y: rectInfo.y,
          width: rectInfo.width,
          height: rectInfo.height
        });

        if (role !== 'iframe' && depth < 12) {
          walk(el, depth + 1);
        }
      } else if (visible && depth < 12) {
        walk(el, depth + 1);
      }
    }
  }

  if (document.body) {
    walk(document.body, 0);
  }

  return JSON.stringify({
    schemaVersion: 1,
    elements: elements
  });
}
