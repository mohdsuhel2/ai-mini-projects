(function (global) {
  const SITE = {
    name: 'NOOBius',
    domain: 'noobius.in',
    origin: 'https://noobius.in',
    defaultImage: 'https://noobius.in/IndianOilLogo.png',
    locale: 'en_IN',
  };

  const GENERATORS = {
    fuel: {
      id: 'fuel',
      path: 'fuel-receipt.html',
      title: 'Fuel Receipt Generator — Free Petrol & Diesel Receipt Maker',
      description:
        'Create fuel receipts online for petrol, diesel, and CNG. Free fuel receipt generator with live preview, bulk PNG export, and Indian fuel station layouts.',
      keywords:
        'fuel receipt generator, petrol receipt generator, diesel receipt maker, gas station receipt, fuel bill generator, petrol bill, diesel bill, CNG receipt, online fuel receipt India, fuel receipt format',
      h1: 'Free Fuel Receipt Generator',
    },
    rent: {
      id: 'rent',
      path: 'rent-receipt.html',
      title: 'Rent Receipt Generator — Free House Rent Receipt PDF',
      description:
        'Generate rent receipts online for house rent, HRA claims, and monthly rent proof. Free rent receipt generator with signature upload and bulk PDF export.',
      keywords:
        'rent receipt generator, house rent receipt, rent receipt format India, monthly rent receipt PDF, HRA rent receipt, landlord receipt generator, rent bill generator',
      h1: 'Free Rent Receipt Generator',
    },
    driver: {
      id: 'driver',
      path: 'driver-slip.html',
      title: 'Driver Slip Generator — Driver Salary Receipt & Payment Slip',
      description:
        'Create driver slips and driver salary receipts online. Free driver slip generator with photo upload, employment details, and bulk PDF export.',
      keywords:
        'driver slip generator, driver salary receipt, driver payment slip, chauffeur receipt, driver bill generator, driver salary slip format India',
      h1: 'Free Driver Slip Generator',
    },
    ecommerce: {
      id: 'ecommerce',
      path: 'ecommerce-invoice.html',
      title: 'Invoice Generator — Free GST Ecommerce Tax Invoice Maker',
      description:
        'Create GST invoices and ecommerce tax invoices online. Free invoice generator with line items, shipping, CGST/SGST, and multi-page PDF export.',
      keywords:
        'invoice generator, GST invoice generator, ecommerce invoice maker, tax invoice generator, bill generator, online invoice India, sales invoice PDF, commercial invoice generator',
      h1: 'Free Invoice & Bill Generator',
    },
    postpaid: {
      id: 'postpaid',
      path: 'postpaid-bill.html',
      title: 'Phone Bill Generator — Mobile & Broadband Postpaid Bill Maker',
      description:
        'Generate postpaid mobile bills, phone bills, and broadband bills online. Free bill generator with payment summary, QR section, and monthly bulk PDF export.',
      keywords:
        'phone bill generator, mobile bill generator, postpaid bill generator, broadband bill generator, internet bill generator, mobile statement generator, telecom bill maker, monthly phone bill PDF',
      h1: 'Free Phone & Internet Bill Generator',
    },
  };

  const STATIC_PAGES = {
    home: {
      path: 'index.html',
      title: 'NOOBius — Free Document Generator for Receipts, Bills & Invoices',
      description:
        'NOOBius is a free online document generator for fuel receipts, rent receipts, driver slips, GST invoices, and postpaid phone bills. Live preview and instant PDF or PNG download.',
      keywords:
        'document generator, receipt generator, bill generator, slip generator, invoice generator, free receipt maker, online document generator India, NOOBius',
      h1: 'Free Online Document Generator',
    },
    about: {
      path: 'about.html',
      title: 'About NOOBius — Free Receipt, Bill & Invoice Generator',
      description:
        'Learn about NOOBius, a browser-based document generator for fuel receipts, rent receipts, driver slips, ecommerce invoices, and postpaid bills with live preview and export.',
      keywords:
        'about NOOBius, document generator, receipt maker, bill generator, invoice generator, online document tools',
      h1: 'About NOOBius Document Generator',
    },
    notFound: {
      path: '404.html',
      title: 'Page Not Found — NOOBius',
      description: 'The page you requested was not found. Browse NOOBius generators for fuel receipts, bills, invoices, and more.',
      keywords: 'NOOBius, document generator',
      h1: 'Page not found',
      robots: 'noindex, follow',
    },
  };

  const PATH_TO_GENERATOR = {
    'fuel-receipt.html': 'fuel',
    'rent-receipt.html': 'rent',
    'driver-slip.html': 'driver',
    'ecommerce-invoice.html': 'ecommerce',
    'postpaid-bill.html': 'postpaid',
    'generator.html': 'fuel',
  };

  function getOrigin() {
    if (global.location && global.location.origin && global.location.protocol !== 'file:') {
      return global.location.origin.replace(/\/$/, '');
    }
    return SITE.origin;
  }

  function absoluteUrl(path) {
    const clean = String(path || '').replace(/^\//, '');
    return `${getOrigin()}/${clean}`;
  }

  function upsertMeta(attr, key, value) {
    if (!value) return;
    let el = document.querySelector(`meta[${attr}="${key}"]`);
    if (!el) {
      el = document.createElement('meta');
      el.setAttribute(attr, key);
      document.head.appendChild(el);
    }
    el.setAttribute('content', value);
  }

  function upsertLink(rel, href) {
    if (!href) return;
    let el = document.querySelector(`link[rel="${rel}"]`);
    if (!el) {
      el = document.createElement('link');
      el.setAttribute('rel', rel);
      document.head.appendChild(el);
    }
    el.setAttribute('href', href);
  }

  function upsertJsonLd(id, data) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('script');
      el.type = 'application/ld+json';
      el.id = id;
      document.head.appendChild(el);
    }
    el.textContent = JSON.stringify(data);
  }

  function applyPageMeta(page, options) {
    if (!page) return;
    const canonical = absoluteUrl(page.path);
    const robots = page.robots || options?.robots || 'index, follow';
    const image = page.image || SITE.defaultImage;

    document.title = page.title;
    upsertMeta('name', 'description', page.description);
    upsertMeta('name', 'keywords', page.keywords);
    upsertMeta('name', 'robots', robots);
    upsertMeta('name', 'author', SITE.name);
    upsertMeta('name', 'application-name', SITE.name);
    upsertMeta('name', 'theme-color', '#2563eb');
    upsertLink('canonical', canonical);

    upsertMeta('property', 'og:site_name', SITE.name);
    upsertMeta('property', 'og:type', 'website');
    upsertMeta('property', 'og:title', page.title);
    upsertMeta('property', 'og:description', page.description);
    upsertMeta('property', 'og:url', canonical);
    upsertMeta('property', 'og:locale', SITE.locale);
    upsertMeta('property', 'og:image', image);

    upsertMeta('name', 'twitter:card', 'summary_large_image');
    upsertMeta('name', 'twitter:title', page.title);
    upsertMeta('name', 'twitter:description', page.description);
    upsertMeta('name', 'twitter:image', image);

    const h1 = document.getElementById('seoPageHeading');
    if (h1 && page.h1) h1.textContent = page.h1;

    upsertJsonLd('json-ld-website', {
      '@context': 'https://schema.org',
      '@type': 'WebSite',
      name: SITE.name,
      url: getOrigin() + '/',
      description: STATIC_PAGES.home.description,
      inLanguage: 'en-IN',
      publisher: {
        '@type': 'Organization',
        name: SITE.name,
        url: getOrigin() + '/',
      },
    });

    if (options?.webApplication) {
      upsertJsonLd('json-ld-webapp', {
        '@context': 'https://schema.org',
        '@type': 'WebApplication',
        name: page.title,
        url: canonical,
        applicationCategory: 'BusinessApplication',
        operatingSystem: 'Any',
        offers: {
          '@type': 'Offer',
          price: '0',
          priceCurrency: 'INR',
        },
        description: page.description,
        browserRequirements: 'Requires JavaScript. Requires HTML5.',
        inLanguage: 'en-IN',
      });
    }
  }

  function getGeneratorIdFromPath() {
    const file = (global.location.pathname.split('/').pop() || '').toLowerCase();
    return PATH_TO_GENERATOR[file] || null;
  }

  function applyGenerator(generatorId) {
    const page = GENERATORS[generatorId];
    if (!page) return;
    applyPageMeta(page, { webApplication: true });
  }

  function applyStatic(pageKey) {
    const page = STATIC_PAGES[pageKey];
    if (!page) return;
    applyPageMeta(page, { webApplication: pageKey === 'home' });
  }

  function initFromPath() {
    const generatorId = getGeneratorIdFromPath();
    if (generatorId) {
      applyGenerator(generatorId);
      return;
    }
    const file = (global.location.pathname.split('/').pop() || 'index.html').toLowerCase();
    if (file === 'about.html') applyStatic('about');
    else if (file === '404.html') applyStatic('notFound');
    else applyStatic('home');
  }

  global.NOOBIUS_SEO = {
    SITE,
    GENERATORS,
    STATIC_PAGES,
    applyGenerator,
    applyStatic,
    initFromPath,
    absoluteUrl,
  };
})(window);
