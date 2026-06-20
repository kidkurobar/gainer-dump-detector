/**
 * Cloudflare Worker — Binance Futures API Proxy
 * Proxies requests to fapi.binance.com
 * Deploy: Cloudflare Dashboard → Workers & Pages → Create → paste this code
 */

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Only allow /fapi/ paths
    if (!url.pathname.startsWith('/fapi/')) {
      return new Response('Binance Futures Proxy — use /fapi/* paths', { status: 200 });
    }

    // Build Binance URL
    const binanceUrl = `https://fapi.binance.com${url.pathname}${url.search}`;

    try {
      const resp = await fetch(binanceUrl, {
        method: request.method,
        headers: {
          'User-Agent': 'Mozilla/5.0',
          'Accept': 'application/json',
        },
      });

      // Forward response with CORS headers
      const body = await resp.text();
      return new Response(body, {
        status: resp.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
          'Cache-Control': 'no-cache',
        },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};
