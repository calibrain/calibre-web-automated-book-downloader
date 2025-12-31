// Settings field types matching backend settings_registry.py

export type FieldType =
  | 'TextField'
  | 'PasswordField'
  | 'NumberField'
  | 'CheckboxField'
  | 'SelectField'
  | 'MultiSelectField'
  | 'OrderableListField'
  | 'ActionButton'
  | 'HeadingField';

export interface SelectOption {
  value: string;
  label: string;
  childOf?: string;  // Parent value - when parent is selected, this option is auto-selected and disabled
}

// Conditional visibility configuration
export interface ShowWhenCondition {
  field: string; // The field key to check
  value?: string | string[]; // The value(s) that make this field visible
  notEmpty?: boolean; // If true, show when field has any non-empty value
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
  default?: string;
}

export interface MultiSelectFieldConfig extends BaseField {
  type: 'MultiSelectField';
  value: string[];
  options: SelectOption[];
  variant?: 'pills' | 'dropdown';  // 'pills' (default) or 'dropdown' for checkbox dropdown style
}

// OrderableListField types - generic drag-and-drop reorderable list
export interface OrderableListItem {
  id: string;
  enabled: boolean;
}

export interface OrderableListOption {
  id: string;
  label: string;
  description?: string;
  disabledReason?: string; // Explanation when item cannot be enabled
  isLocked?: boolean; // Item cannot be toggled (e.g., missing dependency)
}

export interface OrderableListFieldConfig extends BaseField {
  type: 'OrderableListField';
  value: OrderableListItem[];
  options: OrderableListOption[];
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
  showWhen?: ShowWhenCondition; // Conditional visibility based on another field's value
}

// Union type for all fields
export type SettingsField =
  | TextFieldConfig
  | PasswordFieldConfig
  | NumberFieldConfig
  | CheckboxFieldConfig
  | SelectFieldConfig
  | MultiSelectFieldConfig
  | OrderableListFieldConfig
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
