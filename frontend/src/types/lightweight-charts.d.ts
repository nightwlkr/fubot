import type { IChartApiBase, Time, HorzScaleItem, SeriesType, ISeriesApi } from 'lightweight-charts'

declare module 'lightweight-charts' {
  interface IChartApiBase<HorzScaleItem = Time> {
    /**
     * Retrieves the array of series for the current pane (pane 0).
     * Lightweight Charts v4 has this on IChartApiBase but the Default types may not expose it.
     */
    getSeries(): ISeriesApi<SeriesType, HorzScaleItem>[]
  }
}
