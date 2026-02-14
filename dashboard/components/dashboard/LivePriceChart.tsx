'use client';

export function LivePriceChart() {
  return (
    <div className="bg-card border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
        BTC/USDT Price
      </h3>

      <div className="h-64 flex items-center justify-center">
        <p className="text-zinc-500 text-sm">
          Connect live price feed
        </p>
      </div>
    </div>
  );
}
