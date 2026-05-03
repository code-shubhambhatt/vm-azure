import React, { useState, useEffect, useRef, useContext } from 'react';
import { FieldProps } from '@rjsf/utils';
import axios from '@/utils/axios';
import { VMFormDataContext } from '../VMFormDataContext';

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

const AzureVMInstanceField: React.FC<FieldProps<VMInstanceData>> = (props) => {
  const { formData, onChange, name, required } = props;
  const parentFormData = useContext(VMFormDataContext);

  const region = parentFormData?.region || 'us-east';
  const operatingSystem = parentFormData?.operatingSystem || 'linux';
  const linuxType = parentFormData?.linuxType || 'ubuntu';
  const tier = parentFormData?.tier || 'standard';

  const didMountRef = useRef(false);
  const resettingRef = useRef(false);

  const [categories, setCategories] = useState<Category[]>([]);
  const [seriesList, setSeriesList] = useState<Series[]>([]);
  const [sizes, setSizes] = useState<InstanceSize[]>([]);

  const [selectedCat, setSelectedCat] = useState<string>(formData?.category || '');
  const [selectedSeries, setSelectedSeries] = useState<string>(formData?.series || '');
  const [selectedSize, setSelectedSize] = useState<string>(formData?.instanceSize || '');

  const [loadingCats, setLoadingCats] = useState(false);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [loadingSizes, setLoadingSizes] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const notifyChange = (category: string, series: string, instanceSize: string): void => {
    const fullFormData = parentFormData
      ? {
          ...parentFormData,
          instanceSelector: {
            category,
            series,
            instanceSize,
          },
        }
      : {
          category,
          series,
          instanceSize,
        };
    onChange(fullFormData);
  };

  const fetchCategories = async (r: string, os: string): Promise<void> => {
    setLoadingCats(true);
    setError(null);
    try {
      const res = await axios.get(
        `/api/vm/instances/categories?region=${encodeURIComponent(r)}&operatingSystem=${encodeURIComponent(os)}&linuxType=${encodeURIComponent(linuxType)}&tier=${encodeURIComponent(tier)}`
      );
      const incoming: Category[] = res.data.categories || [];
      setCategories(incoming);

      if (selectedCat && !incoming.find((cat) => cat.slug === selectedCat)) {
        setSelectedCat('');
        setSelectedSeries('');
        setSelectedSize('');
        setSeriesList([]);
        setSizes([]);
        notifyChange('', '', '');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load categories');
      setCategories([]);
    } finally {
      setLoadingCats(false);
    }
  };

  const fetchSeries = async (category: string, r: string, os: string): Promise<void> => {
    setLoadingSeries(true);
    setError(null);
    try {
      const res = await axios.get(
        `/api/vm/instances/series?category=${encodeURIComponent(category)}&region=${encodeURIComponent(r)}&operatingSystem=${encodeURIComponent(os)}&linuxType=${encodeURIComponent(linuxType)}&tier=${encodeURIComponent(tier)}`
      );
      const incoming: Series[] = res.data.series || [];
      setSeriesList(incoming);

      if (selectedSeries && !incoming.find((series) => series.slug === selectedSeries)) {
        setSelectedSeries('');
        setSelectedSize('');
        setSizes([]);
        notifyChange(category, '', '');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load series');
      setSeriesList([]);
    } finally {
      setLoadingSeries(false);
    }
  };

  const fetchSizes = async (category: string, series: string, r: string, os: string): Promise<void> => {
    setLoadingSizes(true);
    setError(null);
    try {
      const res = await axios.get(
        `/api/vm/instances/sizes?category=${encodeURIComponent(category)}&series=${encodeURIComponent(series)}&region=${encodeURIComponent(r)}&operatingSystem=${encodeURIComponent(os)}&linuxType=${encodeURIComponent(linuxType)}&tier=${encodeURIComponent(tier)}`
      );
      const incoming: InstanceSize[] = res.data.sizes || [];
      setSizes(incoming);

      if (selectedSize && !incoming.find((size) => size.slug === selectedSize)) {
        setSelectedSize('');
        notifyChange(category, series, '');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sizes');
      setSizes([]);
    } finally {
      setLoadingSizes(false);
    }
  };

  useEffect(() => {
    setSelectedCat(formData?.category || '');
    setSelectedSeries(formData?.series || '');
    setSelectedSize(formData?.instanceSize || '');
  }, [formData]);

  useEffect(() => {
    fetchCategories(region, operatingSystem);
  }, []);

  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true;
      return;
    }

    resettingRef.current = true;
    setSelectedCat('');
    setSelectedSeries('');
    setSelectedSize('');
    setSeriesList([]);
    setSizes([]);
    fetchCategories(region, operatingSystem);
  }, [region, operatingSystem, linuxType, tier]);

  useEffect(() => {
    if (resettingRef.current) {
      if (!selectedCat && !selectedSeries && !selectedSize) {
        resettingRef.current = false;
      }
      return;
    }

    if (selectedCat) {
      fetchSeries(selectedCat, region, operatingSystem);
    } else {
      setSeriesList([]);
      setSelectedSeries('');
      setSizes([]);
      setSelectedSize('');
    }
  }, [selectedCat, region, operatingSystem, linuxType, tier]);

  useEffect(() => {
    if (resettingRef.current) {
      return;
    }

    if (selectedCat && selectedSeries) {
      fetchSizes(selectedCat, selectedSeries, region, operatingSystem);
    } else {
      setSizes([]);
      setSelectedSize('');
    }
  }, [selectedSeries, selectedCat, region, operatingSystem, linuxType, tier]);

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
      fetchCategories(region, operatingSystem);
    } else if (!selectedSeries) {
      fetchSeries(selectedCat, region, operatingSystem);
    } else {
      fetchSizes(selectedCat, selectedSeries, region, operatingSystem);
    }
  };

  const sizeLabel = (s: InstanceSize): string => {
    let label = s.displayName;
    if (s.vcpus !== undefined && s.ram !== undefined) {
      label += ` - ${s.vcpus} vCPU${s.vcpus !== 1 ? 's' : ''}, ${s.ram} GiB RAM`;
    }
    return label;
  };

  return (
    <div className="azure-vm-instance-field">
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
          <option value="">{loadingCats ? 'Loading categories...' : 'Select a category'}</option>
          {categories.map((cat) => (
            <option key={cat.slug} value={cat.slug}>
              {cat.display}
            </option>
          ))}
        </select>
      </div>

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
            {loadingSeries ? 'Loading series...' : selectedCat ? 'Select a series' : 'First select a category'}
          </option>
          {seriesList.map((series) => (
            <option key={series.slug} value={series.slug}>
              {series.display}
            </option>
          ))}
        </select>
      </div>

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
            {loadingSizes ? 'Loading sizes...' : selectedSeries ? 'Select an instance size' : 'First select a series'}
          </option>
          {sizes.map((size) => (
            <option key={size.slug} value={size.slug}>
              {sizeLabel(size)}
            </option>
          ))}
        </select>
        {selectedSize && (
          <small className="form-text text-muted">
            Selected: <strong>{selectedSize}</strong>
          </small>
        )}
      </div>

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
