import React, { useState, useEffect, useRef, useContext } from 'react';
import { FieldProps } from '@rjsf/utils';
import axios from '@/utils/axios';
import { VMFormDataContext } from '../App';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Category {
  slug: string;
  display: string;
}

interface Series {
  slug: string;
  display: string;
}

interface InstanceSize {
  slug: string;
  displayName: string;
  vcpus?: number;
  ram?: number;
}

interface VMInstanceData {
  category: string;
  series: string;
  instanceSize: string;
}

// ── Component ─────────────────────────────────────────────────────────────────

const AzureVMInstanceField: React.FC<FieldProps<VMInstanceData>> = (props) => {
  const { formData, onChange, name, required, formContext } = props;

  // Get full form data from context
  const parentFormData = useContext(VMFormDataContext);
  
  console.log("📦 AzureVMInstanceField - formData:", formData);
  console.log("📦 AzureVMInstanceField - parentFormData from context:", parentFormData);
  console.log("📦 AzureVMInstanceField - formContext:", formContext);

  // Region comes from the parent form via context
  const region: string = parentFormData?.region || 'us-east';

  // Track previous region so we only react to genuine region changes,
  // not spurious re-renders where formContext object reference changes.
  const prevRegionRef = useRef<string>(region);

  // ── State ──────────────────────────────────────────────────────────────────
  const [categories,    setCategories]    = useState<Category[]>([]);
  const [seriesList,    setSeriesList]    = useState<Series[]>([]);
  const [sizes,         setSizes]         = useState<InstanceSize[]>([]);

  const [selectedCat,   setSelectedCat]   = useState<string>(formData?.category     || '');
  const [selectedSeries,setSelectedSeries]= useState<string>(formData?.series       || '');
  const [selectedSize,  setSelectedSize]  = useState<string>(formData?.instanceSize || '');

  const [loadingCats,   setLoadingCats]   = useState<boolean>(false);
  const [loadingSeries, setLoadingSeries] = useState<boolean>(false);
  const [loadingSizes,  setLoadingSizes]  = useState<boolean>(false);
  const [error,         setError]         = useState<string | null>(null);

  useEffect(() => {
    setSelectedCat(formData?.category     || '');
    setSelectedSeries(formData?.series       || '');
    setSelectedSize(formData?.instanceSize || '');
  }, [formData]);

  // ── Fetch categories on mount ──────────────────────────────────────────────
  useEffect(() => {
    fetchCategories();
  }, []);

  // ── Re-fetch series + sizes when parent region genuinely changes ──────────
  useEffect(() => {
    if (prevRegionRef.current === region) return;   // same region — skip
    prevRegionRef.current = region;
    if (selectedCat) {
      setSelectedSeries('');
      setSizes([]);
      setSelectedSize('');
      fetchSeries(selectedCat, region);
    }
  }, [region]);

  // ── Cascade: category change → fetch series ────────────────────────────────
  useEffect(() => {
    if (selectedCat) {
      fetchSeries(selectedCat, region);
    } else {
      setSeriesList([]);
      setSelectedSeries('');
      setSizes([]);
      setSelectedSize('');
    }
  }, [selectedCat]);

  // ── Cascade: series change → fetch sizes ──────────────────────────────────
  useEffect(() => {
    if (selectedCat && selectedSeries) {
      fetchSizes(selectedCat, selectedSeries, region);
    } else {
      setSizes([]);
      setSelectedSize('');
    }
  }, [selectedSeries]);
  // ── API calls ──────────────────────────────────────────────────────────────

  const fetchCategories = async (): Promise<void> => {
    setLoadingCats(true);
    setError(null);
    try {
      const res = await axios.get('/api/vm/instances/categories');
      setCategories(res.data.categories || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load categories');
    } finally {
      setLoadingCats(false);
    }
  };

  const fetchSeries = async (category: string, region: string): Promise<void> => {
    setLoadingSeries(true);
    setError(null);
    try {
      const res = await axios.get(
        `/api/vm/instances/series?category=${encodeURIComponent(category)}&region=${encodeURIComponent(region)}`
      );
      setSeriesList(res.data.series || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load series');
      setSeriesList([]);
    } finally {
      setLoadingSeries(false);
    }
  };

  const fetchSizes = async (category: string, series: string, region: string): Promise<void> => {
    setLoadingSizes(true);
    setError(null);
    try {
      const res = await axios.get(
        `/api/vm/instances/sizes?category=${encodeURIComponent(category)}&series=${encodeURIComponent(series)}&region=${encodeURIComponent(region)}`
      );
      const incoming: InstanceSize[] = res.data.sizes || [];
      setSizes(incoming);

      // If the previously selected size no longer exists in the new list, clear it.
      if (selectedSize && !incoming.find(s => s.slug === selectedSize)) {
        setSelectedSize('');
        notifyChange(category, series, '');
      }    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sizes');
      setSizes([]);
    } finally {
      setLoadingSizes(false);
    }
  };

  // ── rjsf onChange contract ─────────────────────────────────────────────────

  const notifyChange = (category: string, series: string, instanceSize: string): void => {
    // CRITICAL: Pass the ENTIRE parent form data back, with updated instanceSelector.
    // Use data from context to ensure we preserve all fields like region.
    const fullFormData = parentFormData ? {
      ...parentFormData,
      instanceSelector: { 
        category, 
        series, 
        instanceSize 
      }
    } : { 
      category, 
      series, 
      instanceSize 
    };
    console.log("📦 notifyChange sending full formData:", fullFormData);
    console.log("📦 notifyChange - region:", fullFormData?.region);
    onChange(fullFormData);
  };

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleCategoryChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const cat = e.target.value;
    setSelectedCat(cat);
    setSelectedSeries('');
    setSelectedSize('');
    notifyChange(cat, '', '');
  };

  const handleSeriesChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const series = e.target.value;
    setSelectedSeries(series);
    setSelectedSize('');
    notifyChange(selectedCat, series, '');
  };

  const handleSizeChange = (e: React.ChangeEvent<HTMLSelectElement>): void => {
    const size = e.target.value;
    setSelectedSize(size);
    notifyChange(selectedCat, selectedSeries, size);
  };

  const handleRetry = (): void => {
    if (!selectedCat) {
      fetchCategories();
    } else if (!selectedSeries) {
      fetchSeries(selectedCat, region);
    } else {
      fetchSizes(selectedCat, selectedSeries, region);
    }
  };

  // ── Helpers ───────────────────────────────────────────────────────────────

  const sizeLabel = (s: InstanceSize): string => {
    let label = s.displayName;
    if (s.vcpus !== undefined && s.ram !== undefined) {
      label += ` — ${s.vcpus} vCPU${s.vcpus !== 1 ? 's' : ''}, ${s.ram} GiB RAM`;
    }
    return label;
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="azure-vm-instance-field">

      {/* Category */}
      <div className="form-group">
        <label htmlFor={`${name}-category`}>
          Instance Category {required && <span className="required">*</span>}
        </label>
        <select
          id={`${name}-category`}
          className="form-control"
          value={selectedCat}
          onChange={handleCategoryChange}
          disabled={loadingCats}
          required={required}
        >
          <option value="">
            {loadingCats ? 'Loading categories…' : 'Select a category'}
          </option>
          {categories.map(cat => (
            <option key={cat.slug} value={cat.slug}>
              {cat.display}
            </option>
          ))}
        </select>
      </div>

      {/* Series */}
      <div className="form-group">
        <label htmlFor={`${name}-series`}>
          Instance Series {required && <span className="required">*</span>}
        </label>
        <select
          id={`${name}-series`}
          className="form-control"
          value={selectedSeries}
          onChange={handleSeriesChange}
          disabled={!selectedCat || loadingSeries}
          required={required}
        >
          <option value="">
            {loadingSeries
              ? 'Loading series…'
              : selectedCat
                ? 'Select a series'
                : 'First select a category'}
          </option>
          {seriesList.map(s => (
            <option key={s.slug} value={s.slug}>
              {s.display}
            </option>
          ))}
        </select>
      </div>

      {/* Instance Size */}
      <div className="form-group">
        <label htmlFor={`${name}-size`}>
          Instance Size {required && <span className="required">*</span>}
        </label>
        <select
          id={`${name}-size`}
          className="form-control"
          value={selectedSize}
          onChange={handleSizeChange}
          disabled={!selectedSeries || loadingSizes}
          required={required}
        >
          <option value="">
            {loadingSizes
              ? 'Loading sizes…'
              : selectedSeries
                ? 'Select an instance size'
                : 'First select a series'}
          </option>
          {sizes.map(s => (
            <option key={s.slug} value={s.slug}>
              {sizeLabel(s)}
            </option>
          ))}
        </select>
        {selectedSize && (
          <small className="form-text text-muted">
            Selected: <strong>{selectedSize}</strong>
          </small>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="alert alert-danger" role="alert">
          <strong>Error:</strong> {error}
          <button type="button" className="btn btn-sm btn-link" onClick={handleRetry}>
            Retry
          </button>
        </div>
      )}

      <style>{`
        .azure-vm-instance-field {
          margin-bottom: 1rem;
        }

        .form-group {
          margin-bottom: 1rem;
        }

        .form-group label {
          display: block;
          margin-bottom: 0.5rem;
          font-weight: 500;
        }

        .required {
          color: #dc3545;
        }

        .form-control {
          display: block;
          width: 100%;
          padding: 0.375rem 0.75rem;
          font-size: 1rem;
          line-height: 1.5;
          color: #495057;
          background-color: #fff;
          background-clip: padding-box;
          border: 1px solid #ced4da;
          border-radius: 0.25rem;
          transition: border-color 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
        }

        .form-control:disabled {
          background-color: #e9ecef;
          opacity: 1;
          cursor: not-allowed;
        }

        .form-control:focus {
          color: #495057;
          background-color: #fff;
          border-color: #80bdff;
          outline: 0;
          box-shadow: 0 0 0 0.2rem rgba(0, 123, 255, 0.25);
        }

        .form-text {
          display: block;
          margin-top: 0.25rem;
        }

        .text-muted {
          color: #6c757d;
        }

        .alert {
          position: relative;
          padding: 0.75rem 1.25rem;
          margin-bottom: 1rem;
          border: 1px solid transparent;
          border-radius: 0.25rem;
        }

        .alert-danger {
          color: #721c24;
          background-color: #f8d7da;
          border-color: #f5c6cb;
        }

        .btn-link {
          font-weight: 400;
          color: #007bff;
          text-decoration: none;
          background-color: transparent;
          border: none;
          padding: 0;
          margin-left: 0.5rem;
          cursor: pointer;
        }

        .btn-link:hover {
          color: #0056b3;
          text-decoration: underline;
        }
      `}</style>
    </div>
  );
};

export default AzureVMInstanceField;