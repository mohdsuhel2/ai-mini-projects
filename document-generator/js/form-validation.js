(function (global) {
  'use strict';

  function $(id) {
    return document.getElementById(id);
  }

  function clearErrors(root = document) {
    root.querySelectorAll('.field-error-msg').forEach((el) => el.remove());
    root.querySelectorAll('.field-invalid').forEach((el) => el.classList.remove('field-invalid'));
    root.querySelectorAll('[aria-invalid="true"]').forEach((el) => el.removeAttribute('aria-invalid'));
  }

  function clearField(id) {
    const field = $(id);
    if (!field) return;
    const group = field.closest('.field-group');
    group?.classList.remove('field-invalid');
    group?.querySelector('.field-error-msg')?.remove();
    field.removeAttribute('aria-invalid');
  }

  function showError(id, message) {
    const field = $(id);
    if (!field) return null;
    const group = field.closest('.field-group') || field.parentElement;
    if (!group) return field;
    group.classList.add('field-invalid');
    let msg = group.querySelector('.field-error-msg');
    if (!msg) {
      msg = document.createElement('p');
      msg.className = 'field-error-msg';
      group.appendChild(msg);
    }
    msg.textContent = message;
    field.setAttribute('aria-invalid', 'true');
    return field;
  }

  function requireText(id, label) {
    const value = String($(id)?.value || '').trim();
    if (!value) {
      showError(id, `${label} is required.`);
      return false;
    }
    return true;
  }

  function requirePositiveNumber(id, label) {
    const raw = String($(id)?.value ?? '').trim();
    if (!raw) {
      showError(id, `${label} is required.`);
      return false;
    }
    const num = Number(raw);
    if (!Number.isFinite(num) || num <= 0) {
      showError(id, `${label} must be greater than 0.`);
      return false;
    }
    return true;
  }

  function finish(result) {
    if (!result.ok && result.firstField) {
      result.firstField.scrollIntoView({ behavior: 'smooth', block: 'center' });
      result.firstField.focus?.();
    }
    return result;
  }

  function runChecks(checks) {
    let ok = true;
    let firstField = null;
    checks.forEach((check) => {
      const pass = check();
      if (!pass && !firstField) {
        const id = check.fieldId;
        firstField = id ? $(id) : null;
      }
      if (!pass) ok = false;
    });
    return { ok, firstField };
  }

  function wrapCheck(fieldId, fn) {
    const check = () => fn();
    check.fieldId = fieldId;
    return check;
  }

  function validateFuelSingle(ctx = {}) {
    clearErrors();
    const txnMode = ctx.txnMode || 'amount';
    const checks = [
      wrapCheck('vehNo', () => requireText('vehNo', 'Vehicle number')),
      wrapCheck('dateTime', () => requireText('dateTime', 'Date & time')),
      wrapCheck('rate', () => requirePositiveNumber('rate', 'Rate per litre')),
    ];
    if (txnMode === 'capacity') {
      checks.push(wrapCheck('fuelCapacity', () => requirePositiveNumber('fuelCapacity', 'Fuel quantity (L)')));
    } else {
      checks.push(wrapCheck('amount', () => requirePositiveNumber('amount', 'Total amount')));
    }
    return finish(runChecks(checks));
  }

  function validateFuelBulk(ctx = {}) {
    clearErrors();
    const txnMode = ctx.txnMode || 'amount';
    const valueMode = ctx.valueMode || 'fixed';
    const checks = [
      wrapCheck('bulkDateFrom', () => requireText('bulkDateFrom', 'Date from')),
      wrapCheck('bulkDateTo', () => requireText('bulkDateTo', 'Date to')),
      wrapCheck('bulkMinTotalAmount', () => requirePositiveNumber('bulkMinTotalAmount', 'Minimum total amount')),
      wrapCheck('bulkVehNo', () => requireText('bulkVehNo', 'Vehicle number')),
      wrapCheck('bulkRate', () => requirePositiveNumber('bulkRate', 'Rate per litre')),
    ];

    if (valueMode === 'random') {
      if (txnMode === 'capacity') {
        checks.push(
          wrapCheck('bulkCapacityMin', () => requirePositiveNumber('bulkCapacityMin', 'Minimum litres')),
          wrapCheck('bulkCapacityMax', () => requirePositiveNumber('bulkCapacityMax', 'Maximum litres')),
        );
      } else {
        checks.push(
          wrapCheck('bulkAmountMin', () => requirePositiveNumber('bulkAmountMin', 'Minimum amount')),
          wrapCheck('bulkAmountMax', () => requirePositiveNumber('bulkAmountMax', 'Maximum amount')),
        );
      }
    } else if (txnMode === 'capacity') {
      checks.push(wrapCheck('bulkCapacity', () => requirePositiveNumber('bulkCapacity', 'Litres per receipt')));
    } else {
      checks.push(wrapCheck('bulkAmount', () => requirePositiveNumber('bulkAmount', 'Amount per receipt')));
    }

    return finish(runChecks(checks));
  }

  function validatePostpaidSingle() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('bbCustomerName', () => requireText('bbCustomerName', 'Customer name')),
      wrapCheck('bbAddress', () => requireText('bbAddress', 'Address')),
      wrapCheck('bbPhone', () => requireText('bbPhone', 'Phone number')),
      wrapCheck('bbPlanName', () => requireText('bbPlanName', 'Plan name')),
      wrapCheck('bbPlanCharges', () => requirePositiveNumber('bbPlanCharges', 'Plan charges')),
      wrapCheck('bbStatementDate', () => requireText('bbStatementDate', 'Statement date')),
    ]));
  }

  function validatePostpaidBulk() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('bbBulkDateFrom', () => requireText('bbBulkDateFrom', 'First statement date')),
      wrapCheck('bbBulkCount', () => requirePositiveNumber('bbBulkCount', 'Total bills')),
      wrapCheck('bbBulkCustomerName', () => requireText('bbBulkCustomerName', 'Customer name')),
      wrapCheck('bbBulkAddress', () => requireText('bbBulkAddress', 'Address')),
      wrapCheck('bbBulkPhone', () => requireText('bbBulkPhone', 'Phone number')),
      wrapCheck('bbBulkPlanName', () => requireText('bbBulkPlanName', 'Plan name')),
      wrapCheck('bbBulkPlanCharges', () => requirePositiveNumber('bbBulkPlanCharges', 'Plan charges')),
    ]));
  }

  function countInclusiveRentMonths(fromValue, toValue) {
    const matchFrom = String(fromValue || '').trim().match(/^(\d{4})-(\d{2})$/);
    const matchTo = String(toValue || fromValue || '').trim().match(/^(\d{4})-(\d{2})$/);
    if (!matchFrom || !matchTo) return 1;
    const fromIndex = Number(matchFrom[1]) * 12 + (Number(matchFrom[2]) - 1);
    const toIndex = Number(matchTo[1]) * 12 + (Number(matchTo[2]) - 1);
    if (toIndex < fromIndex) return 0;
    return toIndex - fromIndex + 1;
  }

  function requireRentPeriodOrder() {
    const from = document.getElementById('rrPeriodFrom')?.value || '';
    const to = document.getElementById('rrPeriodTo')?.value || '';
    if (!from || !to) return true;
    if (countInclusiveRentMonths(from, to) === 0) {
      showError('rrPeriodTo', '“To” month must be the same as or after “From” month.');
      return false;
    }
    return true;
  }

  function validateRentSingle() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('rrReceiptDate', () => requireText('rrReceiptDate', 'Receipt date')),
      wrapCheck('rrMonthlyRent', () => requirePositiveNumber('rrMonthlyRent', 'Monthly rent')),
      wrapCheck('rrPeriodFrom', () => requireText('rrPeriodFrom', 'Period from month')),
      wrapCheck('rrPeriodTo', () => requireText('rrPeriodTo', 'Period to month')),
      wrapCheck('rrPeriodTo', requireRentPeriodOrder),
      wrapCheck('rrTenantName', () => requireText('rrTenantName', 'Tenant name')),
      wrapCheck('rrHouseNo', () => requireText('rrHouseNo', 'House number')),
      wrapCheck('rrPropertyAddress', () => requireText('rrPropertyAddress', 'Property address')),
      wrapCheck('rrLandlordName', () => requireText('rrLandlordName', 'Owner name')),
    ]));
  }

  function validateRentBulk() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('rrBulkDateFrom', () => requireText('rrBulkDateFrom', 'First receipt date')),
      wrapCheck('rrBulkCount', () => requirePositiveNumber('rrBulkCount', 'Total receipts')),
      wrapCheck('rrBulkTenantName', () => requireText('rrBulkTenantName', 'Tenant name')),
      wrapCheck('rrBulkHouseNo', () => requireText('rrBulkHouseNo', 'House number')),
      wrapCheck('rrBulkPropertyAddress', () => requireText('rrBulkPropertyAddress', 'Property address')),
      wrapCheck('rrBulkLandlordName', () => requireText('rrBulkLandlordName', 'Owner name')),
      wrapCheck('rrBulkRentAmount', () => requirePositiveNumber('rrBulkRentAmount', 'Rent amount')),
      wrapCheck('rrBulkMonthlyRent', () => requirePositiveNumber('rrBulkMonthlyRent', 'Monthly rent')),
    ]));
  }

  function validateDriverSingle() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('dsSlipDate', () => requireText('dsSlipDate', 'Receipt date')),
      wrapCheck('dsReceivedFrom', () => requireText('dsReceivedFrom', 'Received from')),
      wrapCheck('dsDriverName', () => requireText('dsDriverName', 'Driver name')),
      wrapCheck('dsLicenseNo', () => requireText('dsLicenseNo', 'DL number')),
      wrapCheck('dsVehicleNo', () => requireText('dsVehicleNo', 'Vehicle number')),
      wrapCheck('dsSalaryAmount', () => requirePositiveNumber('dsSalaryAmount', 'Salary amount')),
    ]));
  }

  function validateDriverBulk() {
    clearErrors();
    return finish(runChecks([
      wrapCheck('dsBulkDateFrom', () => requireText('dsBulkDateFrom', 'First receipt date')),
      wrapCheck('dsBulkCount', () => requirePositiveNumber('dsBulkCount', 'Total slips')),
      wrapCheck('dsBulkReceivedFrom', () => requireText('dsBulkReceivedFrom', 'Received from')),
      wrapCheck('dsBulkDriverName', () => requireText('dsBulkDriverName', 'Driver name')),
      wrapCheck('dsBulkLicenseNo', () => requireText('dsBulkLicenseNo', 'DL number')),
      wrapCheck('dsBulkVehicleNo', () => requireText('dsBulkVehicleNo', 'Vehicle number')),
      wrapCheck('dsBulkSalaryAmount', () => requirePositiveNumber('dsBulkSalaryAmount', 'Monthly salary')),
    ]));
  }

  function validateEcommerce(itemCount = 0) {
    clearErrors();
    const checks = [
      wrapCheck('ecOrderDate', () => requireText('ecOrderDate', 'Order date')),
      wrapCheck('ecSellerName', () => requireText('ecSellerName', 'Seller name')),
      wrapCheck('ecSellerAddress', () => requireText('ecSellerAddress', 'Seller dispatch address')),
      wrapCheck('ecCustomerName', () => requireText('ecCustomerName', 'Customer name')),
      wrapCheck('ecBillingAddress', () => requireText('ecBillingAddress', 'Billing address')),
      wrapCheck('ecShippingAddress', () => requireText('ecShippingAddress', 'Shipping address')),
    ];
    const result = runChecks(checks);
    if (result.ok && (!itemCount || itemCount < 1)) {
      const list = $('ecLineItemsList');
      if (list) {
        list.classList.add('field-invalid');
        let msg = list.parentElement?.querySelector('.field-error-msg');
        if (!msg) {
          msg = document.createElement('p');
          msg.className = 'field-error-msg';
          list.parentElement?.appendChild(msg);
        }
        msg.textContent = 'Add at least one line item before downloading.';
        result.ok = false;
        result.firstField = list;
      }
    }
    return finish(result);
  }

  function bindClearOnInput(root = document) {
    if (root.dataset.validationBound) return;
    root.dataset.validationBound = '1';
    root.addEventListener('input', (e) => {
      const target = e.target;
      if (!target?.id) return;
      clearField(target.id);
    });
    root.addEventListener('change', (e) => {
      const target = e.target;
      if (!target?.id) return;
      clearField(target.id);
    });
  }

  global.NOOBIUS_FORM_VALIDATE = {
    clearErrors,
    clearField,
    validateFuelSingle,
    validateFuelBulk,
    validatePostpaidSingle,
    validatePostpaidBulk,
    validateRentSingle,
    validateRentBulk,
    validateDriverSingle,
    validateDriverBulk,
    validateEcommerce,
    bindClearOnInput,
  };
})(window);
