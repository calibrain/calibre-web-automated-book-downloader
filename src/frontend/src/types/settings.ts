// Settings field types matching backend settings_registry.py

export type FieldType =
  | 'TextField'
  | 'PasswordField'
  | 'NumberField'
  | 'CheckboxField'
  | 'SelectField'
  | 'MultiSelectField'
  | 'ActionButton'
  | 'HeadingField';

export interface SelectOption {
  value: string;
  label: string;
}

// Conditional visibility configuration
export interface ShowWhenCondition {
  field: string; // The field key to check
  value: string | string[]; // The value(s) that make this field visible
}

// Conditional disable configuration
export interface DisabledWhenCondition {
  field: string; // The field key to check
  value: string | string[] | boolean; // The value(s) that disable this field
  reason?: string; // Explanation shown when disabled
}

// Base field interface - common properties
export interface BaseField {
  key: string;
  label: string;
  type: FieldType;
  description?: string;
  required?: boolean;
  fromEnv?: boolean; // True if value is set via environment variable
  disabled?: boolean; // True if field is disabled/greyed out
  disabledReason?: string; // Explanation shown when field is disabled
  showWhen?: ShowWhenCondition; // Conditional visibility based on another field's value
  disabledWhen?: DisabledWhenCondition; // Conditional disable based on another field's value
  requiresRestart?: boolean; // True if changing this setting requires a container restart
}

// Specific field interfaces
export interface TextFieldConfig extends BaseField {
  type: 'TextField';
  value: string;
  placeholder?: string;
  maxLength?: number;
}

export interface PasswordFieldConfig extends BaseField {
  type: 'PasswordField';
  value: string;
  placeholder?: string;
}

export interface NumberFieldConfig extends BaseField {
  type: 'NumberField';
  value: number;
  min?: number;
  max?: number;
  step?: number;
}

export interface CheckboxFieldConfig extends BaseField {
  type: 'CheckboxField';
  value: boolean;
}

export interface SelectFieldConfig extends BaseField {
  type: 'SelectField';
  value: string;
  options: SelectOption[];
}

export interface MultiSelectFieldConfig extends BaseField {
  type: 'MultiSelectField';
  value: string[];
  options: SelectOption[];
}

export interface ActionButtonConfig extends BaseField {
  type: 'ActionButton';
  style: 'default' | 'primary' | 'danger';
}

export interface HeadingFieldConfig {
  key: string;
  type: 'HeadingField';
  title: string;
  description?: string;
  linkUrl?: string;
  linkText?: string;
}

// Union type for all fields
export type SettingsField =
  | TextFieldConfig
  | PasswordFieldConfig
  | NumberFieldConfig
  | CheckboxFieldConfig
  | SelectFieldConfig
  | MultiSelectFieldConfig
  | ActionButtonConfig
  | HeadingFieldConfig;

// Settings tab structure
export interface SettingsTab {
  name: string; // Internal identifier
  displayName: string; // UI display name
  icon?: string; // Icon name
  order: number; // Sort order
  group?: string; // Group this tab belongs to
  fields: SettingsField[];
}

// Settings group structure
export interface SettingsGroup {
  name: string; // Internal identifier
  displayName: string; // UI display name
  icon?: string; // Icon name
  order: number; // Sort order
}

// API response types
export interface SettingsResponse {
  tabs: SettingsTab[];
  groups: SettingsGroup[];
}

export interface ActionResult {
  success: boolean;
  message: string;
}

export interface UpdateResult {
  success: boolean;
  message: string;
  updated: string[]; // Keys that were updated
  requiresRestart?: boolean; // True if any updated setting requires a restart
  restartRequiredFor?: string[]; // Keys of settings that require restart
}

// Form values type - maps field keys to their values
export type SettingsValues = Record<string, Record<string, unknown>>;
