// Settings field types matching backend settings_registry.py

type FieldType =
  | 'TextField'
  | 'PasswordField'
  | 'NumberField'
  | 'CheckboxField'
  | 'SelectField'
  | 'MultiSelectField'
  | 'TagListField'
  | 'OrderableListField'
  | 'TableField'
  | 'CustomComponentField'
  | 'ActionButton'
  | 'HeadingField';

export interface SelectOption {
  value: string;
  label: string;
  description?: string; // Optional description shown below the label in dropdowns
  childOf?: string; // Parent value - when parent is selected, this option is auto-selected and disabled
}

// Conditional visibility configuration
export interface ShowWhenCondition {
  field: string; // The field key to check
  value?: string | string[]; // The value(s) that make this field visible
  notEmpty?: boolean; // If true, show when field has any non-empty value
}

type ShowWhen = ShowWhenCondition | ShowWhenCondition[];

// Conditional disable configuration
interface DisabledWhenCondition {
  field: string; // The field key to check
  value: string | string[] | boolean; // The value(s) that disable this field
  reason?: string; // Explanation shown when disabled
}

// Base field interface - common properties
interface BaseField {
  key: string;
  label: string;
  type: FieldType;
  description?: string;
  required?: boolean;
  fromEnv?: boolean; // True if value is set via environment variable
  disabled?: boolean; // True if field is disabled/greyed out
  disabledReason?: string; // Explanation shown when field is disabled
  showWhen?: ShowWhen; // Conditional visibility based on another field's value
  disabledWhen?: DisabledWhenCondition; // Conditional disable based on another field's value
  requiresRestart?: boolean; // True if changing this setting requires a container restart
  userOverridable?: boolean; // True when this field supports per-user overrides
  universalOnly?: boolean; // Only show in Universal search mode (hide in Direct mode)
  hiddenInUi?: boolean; // Keep value in schema/save path but hide default renderer
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
  filterByField?: string; // Field key whose value filters options via childOf property
}

export interface MultiSelectFieldConfig extends BaseField {
  type: 'MultiSelectField';
  value: string[];
  options: SelectOption[];
  variant?: 'pills' | 'dropdown'; // 'pills' (default) or 'dropdown' for checkbox dropdown style
  placeholder?: string;
}

export interface TagListFieldConfig extends BaseField {
  type: 'TagListField';
  value: string[];
  placeholder?: string;
  normalizeUrls?: boolean;
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
  isPinned?: boolean; // Item cannot be reordered (but can still be toggled if not locked)
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

export interface CustomComponentFieldConfig extends BaseField {
  type: 'CustomComponentField';
  component: string; // Frontend custom component registry key
  bindKeys?: string[]; // Related value keys this component edits
  boundFields?: SettingsField[]; // Backing value schema edited by this component
  wrapInFieldWrapper?: boolean; // Whether to render with standard FieldWrapper layout
}

interface TableFieldColumnOption {
  value: string;
  label: string;
  description?: string;
  childOf?: string;
}

type TableFieldColumnType = 'text' | 'password' | 'select' | 'multiselect' | 'checkbox' | 'path';

export interface TableFieldColumn {
  key: string;
  label: string;
  type: TableFieldColumnType;
  placeholder?: string;
  options?: TableFieldColumnOption[];
  defaultValue?: string | string[] | boolean;
  filterByField?: string;
}

export interface TableFieldConfig extends BaseField {
  type: 'TableField';
  value: Record<string, unknown>[];
  columns: TableFieldColumn[];
  addLabel?: string;
  emptyMessage?: string;
}

export interface HeadingFieldConfig {
  key: string;
  type: 'HeadingField';
  title: string;
  description?: string;
  descriptionByAuthMode?: Record<string, string>;
  linkUrl?: string;
  linkText?: string;
  showWhen?: ShowWhen; // Conditional visibility based on another field's value
  universalOnly?: boolean; // Only show in Universal search mode (hide in Direct mode)
}

// Union type for all fields
export type SettingsField =
  | TextFieldConfig
  | PasswordFieldConfig
  | NumberFieldConfig
  | CheckboxFieldConfig
  | SelectFieldConfig
  | MultiSelectFieldConfig
  | TagListFieldConfig
  | OrderableListFieldConfig
  | TableFieldConfig
  | CustomComponentFieldConfig
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
  details?: string[];
}

export interface UpdateResult {
  success: boolean;
  message: string;
  updated: string[]; // Keys that were updated
  requiresRestart?: boolean; // True if any updated setting requires a restart
  restartRequiredFor?: string[]; // Keys of settings that require restart
}
