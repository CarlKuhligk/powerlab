"""White, vector-based print chart of the server-computed runtime uncertainty."""
import io
import math
import textwrap
from threading import Lock

import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator


# Matplotlib's font/rendering internals are shared across FastAPI worker threads.
_RENDER_LOCK = Lock()


def render_chart(point, estimate):
    with _RENDER_LOCK:
        return _render_chart(point, estimate)


def _render_chart(point, estimate):
    center = point['expected_h']
    factor, unit = ((8760, 'Jahre') if center >= 8760 else
                    (730, 'Monate') if center >= 2160 else
                    (168, 'Wochen') if center >= 336 else
                    (24, 'Tage') if center >= 48 else (1, 'h'))
    fig = Figure(figsize=(7.0, 3.65), facecolor='white')
    ax = fig.subplots()
    fig.subplots_adjust(left=.13, right=.97, top=.94, bottom=.28)
    ax.set_facecolor('white')
    ax.tick_params(colors='#344451', labelsize=9)
    for edge in ('top', 'right'):
        ax.spines[edge].set_visible(False)
    for edge in ('left', 'bottom'):
        ax.spines[edge].set_color('#a7b5bf')
    ax.grid(color='#e0e7ec', linewidth=.6)
    ax.set_axisbelow(True)
    ax.set_xlabel(f'Batterielaufzeit [{unit}]', fontsize=10, color='#344451', labelpad=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:.4g}'.replace('.', ',')))
    sigma = math.sqrt(estimate['log_variance']) if estimate['available'] else 0
    if math.isfinite(center):
        ax.axvline(center / factor, color='#16713b', linewidth=2,
                   label='Laufzeitschätzung', zorder=4)
        if sigma > 1e-12:
            # The same ±4 log-standard-uncertainty range as in the calculator.
            z = np.linspace(-4, 4, 801)
            hours = center * np.exp(z * sigma)
            positive = hours > 0
            hours, z = hours[positive], z[positive]
            density = np.exp(-.5 * z**2 - np.log(hours) - math.log(sigma)
                             - .5 * math.log(2 * math.pi)) * factor * 100
            x = hours / factor
            ax.plot(x, density, color='#2366ac', linewidth=1.8,
                    label='Unsicherheitsdichte')
            low, high = (estimate['percentiles_h'][p] / factor for p in ('5', '95'))
            ax.axvspan(low, high, facecolor='#2366ac', alpha=.13,
                       label='90-%-Intervall (P5–P95)')
            for bound in (low, high):
                ax.axvline(bound, color='#2366ac', linewidth=1, linestyle='--')
            ax.set_xlim(x[0], x[-1])
            if sigma > 1:
                ax.set_xscale('log')
                ax.set_xlabel(f'Batterielaufzeit [{unit}] · logarithmische Achse',
                              fontsize=10, color='#344451', labelpad=8)
            else:
                ax.xaxis.set_major_locator(MaxNLocator(6))
            ax.set_ylim(bottom=0)
            ax.set_ylabel(f'Unsicherheitsdichte [% / {unit}]', fontsize=9,
                          color='#344451', labelpad=8)
        else:
            margin = max(center / factor * .05, 1e-12)
            ax.set_xlim(max(0, center / factor - margin), center / factor + margin)
            ax.set_ylim(0, 1)
            ax.set_yticks([])
            ax.spines['left'].set_visible(False)
            if not estimate['available']:
                note = 'Unsicherheitsintervall nicht schätzbar.\n' + textwrap.fill(estimate['reason'], width=65)
            elif sigma == 0:
                note = 'Keine beobachtete Streuung.\nDas Modellintervall fällt auf die Schätzung zusammen.'
            else:
                note = 'Sehr kleine Unsicherheit.\nDas Intervall liegt unterhalb der Darstellungsauflösung.'
            ax.text(.5, .78, note,
                    transform=ax.transAxes, ha='center', va='center', fontsize=10, color='#344451')
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .01),
                   ncol=2, frameon=False, fontsize=9, labelcolor='#344451')
    else:
        ax.set_axis_off()
        ax.text(.5, .5, 'Kein Verbrauch im Modell\nKeine endliche Laufzeit oder Unsicherheit darstellbar.',
                transform=ax.transAxes, ha='center', va='center', fontsize=12, color='#344451')
    output = io.BytesIO()
    fig.savefig(output, format='svg', facecolor='white', transparent=False,
                metadata={'Date': None})
    return output.getvalue()
