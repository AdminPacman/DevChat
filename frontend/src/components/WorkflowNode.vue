<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { identityStyles, resolveIdentity, avatarFor } from '../utils/crewIdentity.js'
import RichTooltip from './RichTooltip.vue'
import { getNodeHelp } from '../utils/helpContent.js'
import { configStore } from '../utils/configStore.js'

const props = defineProps({
  id: {
    type: String,
    required: true,
  },
  data: {
    type: Object,
    required: true,
  },
  isActive: {
    type: Boolean,
    default: false,
  },
  sprite: {
    type: String,
    default: '',
  },
})

// Expose hover events so parent can highlight edges
defineEmits(['hover', 'leave'])

// Walking animation state
const walkingFrame = ref(2) // Start with frame 2
let walkingInterval = null

// Compute properties for reactivity
const nodeType = computed(() => props.data?.type || 'unknown')
const nodeId = computed(() => props.data?.id || props.id)
const nodeDescription = computed(() => props.data?.description || '')
const isActive = computed(() => props.isActive)
// Identity, not a hash of the type string. See crewIdentity.js for why: the old
// scheme reshuffled every face on refresh and could cast Pac's own gate as any
// of twelve strangers.
const identity = computed(() => resolveIdentity(props.data))
const dynamicStyles = computed(() => identityStyles(props.data))

const nodeHelpContent = computed(() => getNodeHelp(nodeType.value))

const shouldShowTooltip = computed(() => configStore.ENABLE_HELP_TOOLTIPS && nodeHelpContent.value)

// The node's face. Machinery resolves to '' and renders no avatar at all —
// SCRIPTS MEASURE, AGENTS JUDGE, made visible: a node with no face made no
// judgement. That absence is the signal, not an oversight.
const crewAvatar = computed(() => {
  if (identity.value.faceless) return ''
  // Crew with their own artwork (PAC's tricolor mark) are single-frame — asking
  // for a walk cycle would 404 a sprite path that does not exist for them.
  if (identity.value.avatarOverride) return identity.value.avatarOverride
  return avatarFor(props.data, 'D', isActive.value ? walkingFrame.value : 1)
})

// Kept as a fallback so a parent that still hands down an explicit sprite gets
// what it asked for, rather than a blank node during the transition.
const currentSprite = computed(() => crewAvatar.value || props.sprite || '')

// Start/stop walking animation based on active state
const startWalking = () => {
  if (walkingInterval) return

  walkingInterval = setInterval(() => {
    walkingFrame.value = walkingFrame.value === 2 ? 3 : 2
  }, 500) // Alternate every 500ms
}

const stopWalking = () => {
  if (walkingInterval) {
    clearInterval(walkingInterval)
    walkingInterval = null
  }
  walkingFrame.value = 2 // Reset to frame 2
}

// Watch for active state changes
watch(isActive, (newActive) => {
  if (newActive) {
    startWalking()
  } else {
    stopWalking()
  }
})

// Cleanup on unmount
onUnmounted(() => {
  stopWalking()
})
</script>

<template>
  <RichTooltip v-if="shouldShowTooltip" :content="nodeHelpContent" placement="top">
    <div class="workflow-node-container">
      <div v-if="currentSprite" class="workflow-node-sprite" :class="{ 'is-loud': identity.loud }">
        <img :src="currentSprite" :alt="`${nodeId} sprite`" class="node-sprite-image" />
      </div>
      <div
        class="workflow-node"
        :class="{ 'workflow-node-active': isActive }"
        :data-type="nodeType"
        :style="dynamicStyles"
        @mouseenter="$emit('hover', nodeId)"
        @mouseleave="$emit('leave', nodeId)"
      >
        <div class="workflow-node-header">
          <span class="workflow-node-type">{{ nodeType }}</span>
          <span class="workflow-node-id">{{ nodeId }}</span>
        </div>
        <div v-if="nodeDescription" class="workflow-node-description">
          {{ nodeDescription }}
        </div>

        <Handle
          id="source"
          type="source"
          :position="Position.Right"
          class="workflow-node-handle"
        />
        <Handle
          id="target"
          type="target"
          :position="Position.Left"
          class="workflow-node-handle"
        />
      </div>
    </div>
  </RichTooltip>
  <div v-else class="workflow-node-container">
    <div v-if="currentSprite" class="workflow-node-sprite">
      <img :src="currentSprite" :alt="`${nodeId} sprite`" class="node-sprite-image" />
    </div>
    <div
      class="workflow-node"
      :class="{ 'workflow-node-active': isActive }"
      :data-type="nodeType"
      :style="dynamicStyles"
      @mouseenter="$emit('hover', nodeId)"
      @mouseleave="$emit('leave', nodeId)"
    >
      <div class="workflow-node-header">
        <span class="workflow-node-type">{{ nodeType }}</span>
        <span class="workflow-node-id">{{ nodeId }}</span>
      </div>
      <div v-if="nodeDescription" class="workflow-node-description">
        {{ nodeDescription }}
      </div>

      <Handle
        id="source"
        type="source"
        :position="Position.Right"
        class="workflow-node-handle"
      />
      <Handle
        id="target"
        type="target"
        :position="Position.Left"
        class="workflow-node-handle"
      />
    </div>
  </div>
</template>

<style scoped>
.workflow-node-container {
  position: relative;
}

.workflow-node-description {
  /* Ensure long descriptions wrap instead of overflowing */
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 200px;
  display: block;
}

.workflow-node-sprite {
  position: absolute;
  top: -25px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
}

.node-sprite-image {
  width: 32px;
  height: 40px;
  object-fit: contain;
}

/* The human gate is a person, and the run stops dead until they answer. It gets
   more presence than the machinery around it — the design direction asked for
   "the loudest thing on the canvas when waiting". */
.workflow-node-sprite.is-loud {
  top: -34px;
}

.workflow-node-sprite.is-loud .node-sprite-image {
  width: 46px;
  height: 46px;
  filter: drop-shadow(0 0 6px var(--crew-accent, #ff3d34));
}

/* When it is actually this node's turn, the mark pulses. Held to a slow, small
   cycle: this is a canvas someone watches for a long time, and an aggressive
   animation would become something they learn to tune out. */
.workflow-node-active + .workflow-node-sprite.is-loud .node-sprite-image,
.workflow-node-sprite.is-loud:has(+ .workflow-node-active) .node-sprite-image {
  animation: crew-pac-waiting 1.8s ease-in-out infinite;
}

@keyframes crew-pac-waiting {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.12); }
}

@media (prefers-reduced-motion: reduce) {
  .workflow-node-sprite.is-loud .node-sprite-image {
    animation: none;
  }
}
</style>