export interface ExecutionVenue {
  id: string
  label: string
  type: 'dex' | 'cex'
  dataType: string
  dataSource: string
  color: string
  fillModel: string
  takerFee: string
  makerFee: string
  latency: string
  fundingType: string
}

export const EXECUTION_VENUES: ExecutionVenue[] = [
  {
    id: 'drift', label: 'Drift', type: 'dex',
    dataType: 'Funding rates (1h) + Orderbook depth',
    dataSource: 'Free (Drift S3)',
    color: '#e8a849',
    fillModel: 'JIT Auction → DLOB → vAMM',
    takerFee: '10 bps', makerFee: '-2 bps (rebate)',
    latency: '8s ± 5s', fundingType: 'hourly',
  },
  {
    id: 'hyperliquid', label: 'Hyperliquid', type: 'dex',
    dataType: 'Funding rates (1h) + Orderbook depth',
    dataSource: 'Free (HL Archive)',
    color: '#22d3ee',
    fillModel: 'CLOB + HLP Backstop',
    takerFee: '4.5 bps', makerFee: '-1.5 bps (rebate)',
    latency: '0.2s ± 0.1s', fundingType: 'hourly',
  },
  {
    id: 'jupiter', label: 'Jupiter', type: 'dex',
    dataType: 'Borrow rates + Pool impact',
    dataSource: 'Free (On-chain)',
    color: '#c4b5fd',
    fillModel: 'Oracle Price + Keeper Delay',
    takerFee: '6 bps', makerFee: '6 bps (flat)',
    latency: '12s ± 8s', fundingType: 'borrow',
  },
  {
    id: 'binance', label: 'Binance', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#f0b90b',
    fillModel: 'CLOB Walk',
    takerFee: '5 bps', makerFee: '2 bps',
    latency: '0.2s ± 0.1s', fundingType: 'hourly',
  },
  {
    id: 'okx', label: 'OKX', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#a78bfa',
    fillModel: 'CLOB Walk',
    takerFee: '5 bps', makerFee: '2 bps',
    latency: '0.3s ± 0.15s', fundingType: 'hourly',
  },
  {
    id: 'bybit', label: 'Bybit', type: 'cex',
    dataType: 'Funding rates (8h) + Orderbook depth',
    dataSource: 'Requires Tardis API key',
    color: '#57c84d',
    fillModel: 'CLOB Walk + IOC Band',
    takerFee: '5.5 bps', makerFee: '2 bps',
    latency: '0.3s ± 0.15s', fundingType: 'hourly',
  },
]

export const DEX_VENUES = EXECUTION_VENUES.filter(v => v.type === 'dex')
export const CEX_VENUES = EXECUTION_VENUES.filter(v => v.type === 'cex')
export const DEFAULT_EXECUTION_VENUES = ['drift', 'hyperliquid']

export function getVenue(id: string): ExecutionVenue | undefined {
  return EXECUTION_VENUES.find(v => v.id === id)
}
