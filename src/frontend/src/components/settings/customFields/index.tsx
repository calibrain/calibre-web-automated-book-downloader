import type { ComponentType, ReactNode } from 'react';

import { t } from '../../../i18n';
import { NamingTemplateField } from './NamingTemplateField';
import { OidcAdminHint } from './OidcAdminHint';
import { OidcEnvInfo } from './OidcEnvInfo';
import { RequestPolicyGridField } from './RequestPolicyGridField';
import { SettingsLabel } from './SettingsLabel';
import type {
  CustomSettingsFieldLayout,
  CustomSettingsFieldLayoutContext,
  CustomSettingsFieldRendererProps,
} from './types';
import { UsersManagementField } from './UsersManagementField';

type CustomFieldRenderer = ComponentType<CustomSettingsFieldRendererProps>;
type CustomFieldLayoutResolver = (
  context: CustomSettingsFieldLayoutContext,
) => CustomSettingsFieldLayout;

interface CustomFieldDefinition {
  renderer: CustomFieldRenderer;
  getLayout?: CustomFieldLayoutResolver;
}

function isSaveHandler(value: unknown): value is () => void | Promise<void> {
  return typeof value === 'function';
}

const CUSTOM_FIELD_DEFINITIONS: Record<string, CustomFieldDefinition> = {
  users_management: {
    renderer: UsersManagementField,
    getLayout: ({ uiState }) => {
      const routeKind = typeof uiState.routeKind === 'string' ? uiState.routeKind : 'list';
      const isSubpage = routeKind === 'edit-overrides';
      const onSave = isSaveHandler(uiState.onSave) ? uiState.onSave : undefined;
      return {
        takeOverTab: isSubpage,
        saveBar: isSubpage
          ? {
              hasChanges: Boolean(uiState.hasChanges),
              isSaving: Boolean(uiState.isSaving),
              onSave,
            }
          : undefined,
      };
    },
  },
  request_policy_grid: {
    renderer: RequestPolicyGridField,
  },
  naming_template: {
    renderer: NamingTemplateField,
  },
  settings_label: {
    renderer: SettingsLabel,
  },
  oidc_admin_hint: {
    renderer: OidcAdminHint,
  },
  oidc_env_info: {
    renderer: OidcEnvInfo,
  },
};

export const renderCustomSettingsField = (props: CustomSettingsFieldRendererProps): ReactNode => {
  const definition = CUSTOM_FIELD_DEFINITIONS[props.field.component];
  const Renderer = definition?.renderer;
  if (!Renderer) {
    return (
      <p className="text-xs opacity-60">
        {t('Unknown custom settings component: {component}', { component: props.field.component })}
      </p>
    );
  }
  return <Renderer {...props} />;
};

export const getCustomSettingsFieldLayout = (
  context: CustomSettingsFieldLayoutContext,
): CustomSettingsFieldLayout => {
  const definition = CUSTOM_FIELD_DEFINITIONS[context.field.component];
  if (!definition?.getLayout) {
    return {};
  }
  return definition.getLayout(context);
};

export type { CustomSettingsFieldLayout } from './types';
