from pathlib import Path
from sci_helpers.stacked_to_samples import stacked_file_to_wrangled

path = r'C:\Users\marvi\PycharmProjects\sci-cluster\sci-cluster\sci-data\Aplites_XRF_data\MonteCarlo_synthetic_compositions.csv'

print('Input file:', path)

for skip in range(0,8):
    try:
        results = stacked_file_to_wrangled(path, anchor='SiO2', skip_first_cols=skip, auto_detect_skip=False)
        n = len(results)
        first = results[0] if n>0 else None
        print(f'--- skip_first_cols={skip}: datasets={n}')
        if first:
            ox = first.get('oxides_order')
            notes = first.get('notes', [])
            sample0 = first.get('samples_list', [None])[0]
            print(' oxides_order:', ox)
            print(' first_sample:', sample0)
            print(' notes (first 5):', notes[:5])
    except Exception as e:
        print(f'Error for skip={skip}:', e)

# Try auto-detect
try:
    results_auto = stacked_file_to_wrangled(path, anchor='SiO2', skip_first_cols=0, auto_detect_skip=True)
    print('--- auto_detect_skip=True: datasets=', len(results_auto))
    if results_auto:
        f = results_auto[0]
        print(' oxides_order:', f.get('oxides_order'))
        print(' first_sample:', f.get('samples_list', [None])[0])
        print(' notes (first 5):', f.get('notes', [])[:5])
except Exception as e:
    print('Error for auto_detect:', e)
