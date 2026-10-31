---
language:
- en
license: cc-by-4.0
task_categories:
- time-series-forecasting
tags:
- fresh-retail
- censored-demand
- hourly-stock-status
size_categories:
- 1M<n<10M
pretty_name: FreshRetailNet-50K
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train.parquet
  - split: eval
    path: data/eval.parquet
---

# FreshRetailNet-50K

## Dataset Overview
FreshRetailNet-50K is the first large-scale benchmark for censored demand estimation in the fresh retail domain, **incorporating approximately 20% organically occurring stockout data**. It comprises 50,000 store-product 90-day time series of detailed hourly sales data from 898 stores in 18 major cities, encompassing 865 perishable SKUs with meticulous stockout event annotations. The hourly stock status records unique to this dataset, combined with rich contextual covariates including promotional discounts, precipitation, and other temporal features, enable innovative research beyond existing solutions.

- [Technical Report](https://arxiv.org/abs/2505.16319) - Discover the methodology and technical details behind FreshRetailNet-50K.
- [Github Repo](https://github.com/Dingdong-Inc/frn-50k-baseline) - Access the complete pipeline used to train and evaluate.

This dataset is ready for commercial/non-commercial use.


## Data Fields
|Field|Type|Description|
|:---|:---|:---|
|city_id|int64|The encoded city id|
|store_id|int64|The encoded store id|
|management_group_id|int64|The encoded management group id|
|first_category_id|int64|The encoded first category id|
|second_category_id|int64|The encoded second category id|
|third_category_id|int64|The encoded third category id|
|product_id|int64|The encoded product id|
|dt|string|The date|
|sale_amount|float64|The daily sales quantity after global normalization (Multiplied by a specific coefficient)|
|hours_sale|Sequence(float64)|The hourly sales quantity after global normalization (Multiplied by a specific coefficient)|
|stock_hour6_22_cnt|int32|The number of out-of-stock hours between 6:00 and 22:00|
|hours_stock_status|Sequence(int32)|The hourly out-of-stock status|
|discount|float64|The discount rate (1.0 means no discount, 0.9 means 10% off)|
|holiday_flag|int32|Holiday indicator|
|activity_flag|int32|Activity indicator|
|precpt|float64|The total precipitation|
|avg_temperature|float64|The average temperature|
|avg_humidity|float64|The average humidity|
|avg_wind_level|float64|The average wind force|

### Hierarchical structure
- **warehouse**: city_id > store_id
- **product category**: management_group_id > first_category_id > second_category_id > third_category_id > product_id



## How to use it

You can load the dataset with the following lines of code.

```python
from datasets import load_dataset
dataset = load_dataset("Dingdong-Inc/FreshRetailNet-50K")
print(dataset)
```
```text
DatasetDict({
    train: Dataset({
        features: ['city_id', 'store_id', 'management_group_id', 'first_category_id', 'second_category_id', 'third_category_id', 'product_id', 'dt', 'sale_amount', 'hours_sale', 'stock_hour6_22_cnt', 'hours_stock_status', 'discount', 'holiday_flag', 'activity_flag', 'precpt', 'avg_temperature', 'avg_humidity', 'avg_wind_level'],
        num_rows: 4500000
    })
    eval: Dataset({
        features: ['city_id', 'store_id', 'management_group_id', 'first_category_id', 'second_category_id', 'third_category_id', 'product_id', 'dt', 'sale_amount', 'hours_sale', 'stock_hour6_22_cnt', 'hours_stock_status', 'discount', 'holiday_flag', 'activity_flag', 'precpt', 'avg_temperature', 'avg_humidity', 'avg_wind_level'],
        num_rows: 350000
    })
})
```


## License/Terms of Use

This dataset is licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0) available at https://creativecommons.org/licenses/by/4.0/legalcode.

**Data Developer:** Dingdong-Inc


### Use Case: <br>
Developers researching latent demand recovery and demand forecasting techniques. <br>

### Release Date:  <br>
05/08/2025 <br>


## Data Version
1.0 (05/08/2025)


## Intended use

The FreshRetailNet-50K Dataset is intended to be freely used by the community to continue to improve latent demand recovery and demand forecasting techniques.
**However, for each dataset an user elects to use, the user is responsible for checking if the dataset license is fit for the intended purpose**.


## Citation

If you find the data useful, please cite:
```
@article{2025freshretailnet-50k,
      title={FreshRetailNet-50K: A Stockout-Annotated Censored Demand Dataset for Latent Demand Recovery and Forecasting in Fresh Retail},
      author={Yangyang Wang, Jiawei Gu, Li Long, Xin Li, Li Shen, Zhouyu Fu, Xiangjun Zhou, Xu Jiang},
      year={2025},
      eprint={2505.16319},
      archivePrefix={arXiv},
      primaryClass={cs.LG},
      url={https://arxiv.org/abs/2505.16319},
}
```